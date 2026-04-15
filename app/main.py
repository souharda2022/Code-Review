from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os
import traceback

from app.retriever import retrieve_context
from app.prompts import assemble_prompt
from app.llm_client import call_llm, unload_model, MODEL_FAST, MODEL_DEEP
from app.chunker import chunk_code
from app.call_graph import build_call_graph
from app.token_router import route_by_token_count, count_tokens
from app.deep_review import (
    build_critique_prompt, merge_pass1_pass2,
    should_auto_trigger_deep_review, detect_security_patterns,
)
from app.modes import validate_mode
from app.session import sessions
from app.suggestions import (
    add_suggestion, remove_suggestion, toggle_suggestion,
    load_suggestions, get_active_suggestions,
)
from app.language_detect import detect_language
from app.teams import load_teams, add_team, remove_team, get_team

app = FastAPI(title="Code Review Assistant", version="1.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---- Schemas ----------------------------------------------------------------

class ReviewRequest(BaseModel):
    language: str = ""
    code: str
    question: str = "What is wrong with this code?"
    file_name: str = ""
    mode: str = "no"
    team: str = "petclinic-backend"
    session_id: str = ""
    show_reasoning: bool = False
    auto_apply: bool = False

class DeepReviewRequest(BaseModel):
    language: str
    code: str
    pass1_result: dict
    team: str = "petclinic-backend"
    question: str = "What is wrong with this code?"

class SuggestionRequest(BaseModel):
    title: str
    rule: str
    language: str = "all"
    category: str = "custom"
    severity: str = "medium"
    team: str = "all"
    example_bad: str = ""
    example_good: str = ""

class TeamRequest(BaseModel):
    team_id: str
    name: str
    description: str = ""
    languages: list = []
    repo: str = ""

class Issue(BaseModel):
    id: int
    severity: str
    location: str
    problem: str
    explanation: str
    fix: str
    rule_violated: str

class ReviewResponse(BaseModel):
    language: str
    issues: List[Issue]
    summary: str
    style_violations: List[str]
    pass_used: str
    mode: str = "no"
    team: str = ""
    token_info: Optional[dict] = None
    rag_context: Optional[dict] = None
    chunking_info: Optional[dict] = None
    deep_review_suggestion: Optional[dict] = None
    deep_review_stats: Optional[dict] = None
    security_notes: Optional[str] = None
    suggested_code: Optional[str] = None
    updated_code: Optional[str] = None
    changes: Optional[list] = None
    preview: bool = False
    session_id: Optional[str] = None
    reasoning: Optional[str] = None
    language_detection: Optional[dict] = None

class RetrievalTestRequest(BaseModel):
    language: str
    code: str
    team: str = "petclinic-backend"

# ---- Helpers ----------------------------------------------------------------

async def _review_single(code, language, question, team="default",
                         previous_summaries="", mode="no", show_reasoning=False):
    try:
        rag = retrieve_context(code, language, team=team)
    except Exception:
        rag = {"context": "(retrieval error)", "sources": [], "token_count": 0, "categories": {}}
    messages = assemble_prompt(language=language, code=code, question=question,
                               rag_context=rag["context"], previous_summaries=previous_summaries,
                               mode=mode, show_reasoning=show_reasoning)
    llm_resp = await call_llm(messages, model=MODEL_FAST)
    return {"result": llm_resp["result"], "llm": llm_resp, "rag": rag}

def _merge_issues(chunk_results):
    merged_issues, all_violations = [], set()
    total_input = total_output = 0
    sources_seen, all_sources, all_categories = set(), [], {}
    for cr in chunk_results:
        result, llm, rag = cr["result"], cr["llm"], cr["rag"]
        for issue in result.get("issues", []):
            key = (issue.get("problem", ""), issue.get("location", ""))
            if key not in {(i.get("problem",""),i.get("location","")) for i in merged_issues}:
                merged_issues.append(issue)
        for v in result.get("style_violations", []): all_violations.add(v)
        total_input += llm.get("input_tokens", 0)
        total_output += llm.get("output_tokens", 0)
        for src in rag.get("sources", []):
            if src["id"] not in sources_seen: sources_seen.add(src["id"]); all_sources.append(src)
        for cat, score in rag.get("categories", {}).items():
            if cat not in all_categories or score > all_categories[cat]: all_categories[cat] = score
    sev = {"high":0,"medium":1,"low":2}
    merged_issues.sort(key=lambda x: sev.get(x.get("severity","low"),3))
    for i, issue in enumerate(merged_issues): issue["id"] = i+1
    h=sum(1 for i in merged_issues if i.get("severity")=="high")
    m=sum(1 for i in merged_issues if i.get("severity")=="medium")
    l=sum(1 for i in merged_issues if i.get("severity")=="low")
    return {"issues":merged_issues,"summary":f"{len(merged_issues)} issues: {h} high, {m} medium, {l} low.",
            "style_violations":list(all_violations),"total_input_tokens":total_input,
            "total_output_tokens":total_output,"sources":all_sources,"categories":all_categories}

async def _review_chunked(chunks, call_graph, language, question, team="default", mode="no", show_reasoning=False):
    chunk_results, method_summaries = [], []
    for chunk in chunks:
        carry = "\n".join(method_summaries) if method_summaries else ""
        cr = await _review_single(chunk.code, language, question, team, carry, mode, show_reasoning)
        chunk_results.append(cr)
        ms = cr["result"].get("method_summary", "")
        if ms: method_summaries.append(f"- {chunk.method_name}: {ms}")
        else:
            iss = cr["result"].get("issues", [])
            method_summaries.append(f"- {chunk.method_name}: {'has issues' if iss else 'ok'}")
    return chunk_results, method_summaries

# ---- /review ----------------------------------------------------------------

@app.post("/review", response_model=ReviewResponse)
async def review_code(req: ReviewRequest):
    mode = validate_mode(req.mode)
    team = req.team or "default"

    lang_detect = None
    language = req.language
    if not language or language == "auto":
        lang_detect = detect_language(req.code, "")
        language = lang_detect["language"]

    session = sessions.get_or_create(req.session_id)
    session.current_code = req.code
    session.current_language = language
    session.add_message("user", f"[{mode}][{team}] {req.question}", {"mode":mode,"team":team,"language":language})

    routing = route_by_token_count(req.code)

    if routing["route"] == "reject":
        session.add_message("assistant", routing["reason"])
        return ReviewResponse(language=language, issues=[], pass_used="rejected",
                              summary=routing["reason"], style_violations=[], mode=mode, team=team,
                              session_id=session.session_id)

    if routing["route"] == "send_as_is":
        cg = build_call_graph(req.code, language)
        cg_text = cg.format_for_prompt()
        code_with_graph = (cg_text + "\n\n" + req.code) if cg_text else req.code
        cr = await _review_single(code_with_graph, language, req.question, team, mode=mode, show_reasoning=req.show_reasoning)
        result, llm, rag = cr["result"], cr["llm"], cr["rag"]
        auto_trigger = should_auto_trigger_deep_review(req.code, result)

        suggested_code = result.get("suggested_code")
        updated_code = result.get("updated_code")
        changes = result.get("changes")
        reasoning = result.get("reasoning")
        preview = mode == "update" and updated_code and not req.auto_apply

        session.last_review = {"issues":result.get("issues",[]),"summary":result.get("summary",""),"suggested_code":suggested_code,"updated_code":updated_code,"changes":changes}
        if preview: session.last_preview = {"updated_code":updated_code,"changes":changes}
        session.add_message("assistant", result.get("summary",""), {"mode":mode,"issues_count":len(result.get("issues",[])),"model":llm["model"]})

        return ReviewResponse(
            language=result.get("language",language), issues=[Issue(**i) for i in result.get("issues",[])],
            summary=result.get("summary",""), style_violations=result.get("style_violations",[]),
            pass_used="fast", mode=mode, team=team,
            token_info={"input_tokens":llm["input_tokens"],"output_tokens":llm["output_tokens"],"model":llm["model"],"error":llm["error"]},
            rag_context={"retrieved_tokens":rag["token_count"],"sources":rag["sources"],"detected_categories":rag["categories"]},
            chunking_info={"route":"send_as_is","chunks":1,"code_tokens":routing["code_tokens"]},
            deep_review_suggestion=auto_trigger if auto_trigger["suggest"] else None,
            suggested_code=suggested_code, updated_code=updated_code, changes=changes,
            preview=preview, session_id=session.session_id, reasoning=reasoning, language_detection=lang_detect,
        )

    chunks, call_graph = chunk_code(req.code, language, req.file_name)
    chunk_results, method_summaries = await _review_chunked(chunks, call_graph, language, req.question, team, mode, req.show_reasoning)
    merged = _merge_issues(chunk_results)

    all_suggested, all_updated, all_changes = [], [], []
    for cr in chunk_results:
        r = cr["result"]
        if r.get("suggested_code"): all_suggested.append(r["suggested_code"])
        if r.get("updated_code"): all_updated.append(r["updated_code"])
        if r.get("changes"): all_changes.extend(r["changes"])

    auto_trigger = should_auto_trigger_deep_review(req.code, {"issues":merged["issues"]})
    suggested_code = "\n\n// ---\n\n".join(all_suggested) if all_suggested else None
    updated_code = "\n\n// ---\n\n".join(all_updated) if all_updated else None
    preview = mode == "update" and updated_code and not req.auto_apply

    session.last_review = merged
    session.add_message("assistant", merged["summary"], {"mode":mode,"chunks":len(chunks)})

    return ReviewResponse(
        language=language, issues=[Issue(**i) for i in merged["issues"]],
        summary=merged["summary"], style_violations=merged["style_violations"],
        pass_used="fast", mode=mode, team=team,
        token_info={"input_tokens":merged["total_input_tokens"],"output_tokens":merged["total_output_tokens"],"model":MODEL_FAST,"note":f"{len(chunks)} chunks"},
        rag_context={"retrieved_tokens":sum(cr["rag"]["token_count"] for cr in chunk_results),"sources":merged["sources"],"detected_categories":merged["categories"]},
        chunking_info={"route":routing["route"],"chunks":len(chunks),"chunk_details":[{"method":c.method_name,"lines":f"{c.start_line}-{c.end_line}"} for c in chunks]},
        deep_review_suggestion=auto_trigger if auto_trigger["suggest"] else None,
        suggested_code=suggested_code, updated_code=updated_code, changes=all_changes or None,
        preview=preview, session_id=session.session_id, language_detection=lang_detect,
    )

# ---- /review-deep -----------------------------------------------------------

@app.post("/review-deep", response_model=ReviewResponse)
async def review_deep(req: DeepReviewRequest):
    team = req.team or "default"
    try:
        rag = retrieve_context(req.code, req.language, team=team)
    except Exception:
        rag = {"context":"(error)","sources":[],"token_count":0,"categories":{}}
    security = detect_security_patterns(req.code)
    await unload_model(MODEL_FAST)
    messages = build_critique_prompt(req.code, req.language, req.pass1_result, rag["context"], security if security else None)
    llm_resp = await call_llm(messages, model=MODEL_DEEP)
    merged = merge_pass1_pass2(req.pass1_result, llm_resp["result"])
    return ReviewResponse(
        language=req.language, issues=[Issue(**i) for i in merged["issues"]],
        summary=merged["summary"], style_violations=merged["style_violations"],
        pass_used="deep", mode="no", team=team,
        token_info={"input_tokens":llm_resp["input_tokens"],"output_tokens":llm_resp["output_tokens"],"model":llm_resp["model"],"error":llm_resp["error"]},
        rag_context={"retrieved_tokens":rag["token_count"],"sources":rag["sources"],"detected_categories":rag["categories"]},
        deep_review_stats=merged.get("deep_review_stats"), security_notes=merged.get("security_notes",""),
    )

# ---- /review-file -----------------------------------------------------------

@app.post("/review-file", response_model=ReviewResponse)
async def review_file(file: UploadFile = File(...), language: str = Form(""), question: str = Form("What is wrong?"), mode: str = Form("no"), team: str = Form("petclinic-backend")):
    content = await file.read()
    code = content.decode("utf-8", errors="replace")
    file_name = file.filename or "uploaded_file"
    mode = validate_mode(mode)
    if not language or language == "auto":
        language = detect_language(code)["language"]
    routing = route_by_token_count(code)
    if routing["route"] == "reject":
        return ReviewResponse(language=language, issues=[], pass_used="rejected", summary=routing["reason"], style_violations=[], mode=mode, team=team)
    chunks, cg = chunk_code(code, language, file_name)
    chunk_results, _ = await _review_chunked(chunks, cg, language, question, team, mode)
    merged = _merge_issues(chunk_results)
    all_updated, all_changes = [], []
    for cr in chunk_results:
        r = cr["result"]
        if r.get("updated_code"): all_updated.append(r["updated_code"])
        if r.get("changes"): all_changes.extend(r["changes"])
    auto_trigger = should_auto_trigger_deep_review(code, {"issues":merged["issues"]})
    return ReviewResponse(
        language=language, issues=[Issue(**i) for i in merged["issues"]],
        summary=merged["summary"], style_violations=merged["style_violations"],
        pass_used="fast", mode=mode, team=team,
        token_info={"input_tokens":merged["total_input_tokens"],"output_tokens":merged["total_output_tokens"],"model":MODEL_FAST,"note":f"File '{file_name}' {len(chunks)} chunks"},
        rag_context={"retrieved_tokens":sum(cr["rag"]["token_count"] for cr in chunk_results),"sources":merged["sources"],"detected_categories":merged["categories"]},
        chunking_info={"route":routing["route"],"chunks":len(chunks),"file_name":file_name},
        deep_review_suggestion=auto_trigger if auto_trigger["suggest"] else None,
        updated_code="\n\n".join(all_updated) if all_updated else None, changes=all_changes or None,
    )

# ---- /apply-preview ---------------------------------------------------------

@app.post("/apply-preview")
async def apply_preview(session_id: str):
    session = sessions.get_session(session_id)
    if not session or not session.last_preview:
        return {"status":"error","message":"No preview found"}
    preview = session.last_preview
    session.last_preview = None
    return {"status":"applied","updated_code":preview["updated_code"],"changes":preview["changes"]}

# ---- /chat ------------------------------------------------------------------

@app.get("/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    session = sessions.get_session(session_id)
    if not session: return {"messages":[],"session_id":session_id}
    return {"messages":session.get_history(),"session_id":session_id,"info":session.to_dict()}

@app.get("/chat/sessions")
async def list_sessions():
    return {"sessions":sessions.list_sessions()}

# ---- /suggestions -----------------------------------------------------------

@app.get("/suggestions")
async def get_suggestions(language: str = "all", team: str = "all"):
    all_sugs = get_active_suggestions(language)
    if team != "all":
        all_sugs = [s for s in all_sugs if s.get("team","all") in (team, "all")]
    return {"suggestions":all_sugs,"total":len(load_suggestions())}

@app.post("/suggestions")
async def create_suggestion(req: SuggestionRequest):
    entry = add_suggestion(title=req.title, rule=req.rule, language=req.language,
                           category=req.category, severity=req.severity,
                           example_bad=req.example_bad, example_good=req.example_good)
    # Add team tag
    sugs = load_suggestions()
    for s in sugs:
        if s["id"] == entry["id"]:
            s["team"] = req.team
    from app.suggestions import save_suggestions
    save_suggestions(sugs)
    entry["team"] = req.team
    return {"status":"created","suggestion":entry}

@app.delete("/suggestions/{suggestion_id}")
async def delete_suggestion(suggestion_id: str):
    return {"status":"removed" if remove_suggestion(suggestion_id) else "not_found"}

@app.post("/suggestions/{suggestion_id}/toggle")
async def toggle_suggestion_ep(suggestion_id: str):
    result = toggle_suggestion(suggestion_id)
    return {"status":"toggled","suggestion":result} if result else {"status":"not_found"}

# ---- /teams -----------------------------------------------------------------

@app.get("/teams")
async def list_teams():
    return {"teams":load_teams()}

@app.post("/teams")
async def create_team(req: TeamRequest):
    team = add_team(req.team_id, req.name, req.description, req.languages, req.repo)
    return {"status":"created","team":team}

@app.delete("/teams/{team_id}")
async def delete_team_ep(team_id: str):
    return {"status":"removed" if remove_team(team_id) else "not_found"}

# ---- /detect-language -------------------------------------------------------

@app.post("/detect-language")
async def detect_language_ep(code: str = ""):
    return detect_language(code)

# ---- Debug ------------------------------------------------------------------

@app.post("/retrieval-test")
async def retrieval_test(req: RetrievalTestRequest):
    try:
        rag = retrieve_context(req.code, req.language, team=req.team)
        return {"status":"ok","language":req.language,"team":req.team,"token_count":rag["token_count"],"sources":rag["sources"],"context_preview":rag["context"][:2000]}
    except Exception as e:
        return {"status":"error","error":str(e),"trace":traceback.format_exc()}

@app.post("/chunk-test")
async def chunk_test(req: ReviewRequest):
    routing = route_by_token_count(req.code)
    lang = req.language or detect_language(req.code).get("language","java")
    chunks, cg = chunk_code(req.code, lang, req.file_name)
    return {"routing":routing,"total_chunks":len(chunks),"call_graph":cg.format_for_prompt() or "(none)",
            "chunks":[{"method":c.method_name,"lines":f"{c.start_line}-{c.end_line}","tokens":count_tokens(c.code)} for c in chunks]}

@app.get("/health")
async def health():
    return {"status":"ok","version":"1.1.0-multiteam","model_fast":MODEL_FAST,"model_deep":MODEL_DEEP,
            "teams":[t["id"] for t in load_teams()]}

# ---- Frontend ---------------------------------------------------------------

ui_dir = os.path.join(os.path.dirname(__file__), "..", "ui")
app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

@app.get("/")
async def root():
    return FileResponse(os.path.join(ui_dir, "index.html"))
