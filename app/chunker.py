"""
Code chunker: splits files at semantic boundaries, not character counts.

Java:       Class -> Method level
TypeScript: Component/Service -> Method/Lifecycle hook level

Each chunk includes:
  1. Class-level context (file name, annotations, dependencies)
  2. Call graph (which methods call which, null contracts)

For large methods (>80 lines), splits at logical block boundaries.
"""

import re
from dataclasses import dataclass, field
from app.call_graph import build_call_graph, CallGraph


@dataclass
class CodeChunk:
    """One reviewable unit."""
    file_name: str
    language: str
    class_name: str
    class_context: str
    method_name: str
    code: str                 # full text sent to LLM (context + call graph + method)
    start_line: int
    end_line: int
    chunk_index: int = 0
    total_chunks: int = 1
    is_partial: bool = False


# ---- Class Context Extraction -----------------------------------------------

def _extract_java_class_context(code: str, file_name: str) -> dict:
    lines = code.split("\n")

    package = ""
    for line in lines:
        m = re.match(r"\s*package\s+([\w.]+)\s*;", line)
        if m:
            package = m.group(1)
            break

    class_annotations = []
    class_name = ""
    class_extends = ""
    class_implements = ""

    for i, line in enumerate(lines):
        cm = re.match(
            r"\s*(?:public\s+)?(?:abstract\s+)?class\s+(\w+)"
            r"(?:\s+extends\s+(\w+))?"
            r"(?:\s+implements\s+([\w,\s]+))?\s*\{",
            line,
        )
        if cm:
            class_name = cm.group(1)
            class_extends = cm.group(2) or ""
            class_implements = cm.group(3) or ""
            class_annotations = []
            for j in range(max(0, i - 5), i):
                a = re.match(r"\s*(@\w+(?:\([^)]*\))?)", lines[j])
                if a:
                    class_annotations.append(a.group(1))
            break

    dependencies = []
    for line in lines:
        dm = re.match(r"\s*private\s+(?:final\s+)?(\w+)\s+(\w+)\s*;", line)
        if dm:
            dependencies.append(f"{dm.group(1)} {dm.group(2)}")

    return {
        "file_name": file_name,
        "package": package,
        "class_name": class_name or file_name.replace(".java", ""),
        "class_annotations": class_annotations,
        "extends": class_extends,
        "implements": class_implements,
        "dependencies": dependencies[:10],
    }


def _extract_ts_class_context(code: str, file_name: str) -> dict:
    decorator = ""
    dm = re.search(r"@(Component|Injectable|Directive|Pipe|NgModule)", code)
    if dm:
        decorator = f"@{dm.group(1)}"

    selector = ""
    sm = re.search(r"selector:\s*['\"]([^'\"]+)", code)
    if sm:
        selector = sm.group(1)

    class_name = file_name.replace(".ts", "")
    cm = re.search(r"export\s+class\s+(\w+)", code)
    if cm:
        class_name = cm.group(1)

    implements = ""
    im = re.search(r"implements\s+([\w,\s]+)\s*\{", code)
    if im:
        implements = im.group(1).strip()

    deps = []
    ctor = re.search(r"constructor\s*\(([\s\S]*?)\)\s*\{", code)
    if ctor:
        params = ctor.group(1)
        for pm in re.finditer(r"(?:private|protected|public)\s+(\w+)\s*:\s*(\w+)", params):
            deps.append(f"{pm.group(2)} {pm.group(1)}")

    return {
        "file_name": file_name,
        "class_name": class_name,
        "decorator": decorator,
        "selector": selector,
        "implements": implements,
        "dependencies": deps[:10],
    }


def _build_context_header(ctx: dict, language: str) -> str:
    header = "[FILE CONTEXT]\n"
    header += f"File: {ctx['file_name']}\n"
    if ctx.get("package"):
        header += f"Package: {ctx['package']}\n"
    header += f"Class: {ctx['class_name']}\n"
    if ctx.get("class_annotations"):
        header += f"Class annotations: {', '.join(ctx['class_annotations'])}\n"
    if ctx.get("decorator"):
        header += f"Decorator: {ctx['decorator']}\n"
    if ctx.get("selector"):
        header += f"Selector: {ctx['selector']}\n"
    if ctx.get("extends"):
        header += f"Extends: {ctx['extends']}\n"
    if ctx.get("implements"):
        header += f"Implements: {ctx['implements']}\n"
    if ctx.get("dependencies"):
        header += f"Dependencies: {', '.join(ctx['dependencies'])}\n"
    return header


# ---- Method Finding ---------------------------------------------------------

def _find_java_methods(code: str) -> list[dict]:
    lines = code.split("\n")
    methods = []
    i = 0
    while i < len(lines):
        line = lines[i]
        sig_match = re.match(
            r"\s*(?:@\w+(?:\([^)]*\))?\s*)*"
            r"(public|protected|private)\s+"
            r"(?:static\s+)?(?:final\s+)?"
            r"(?:synchronized\s+)?"
            r"(?:<[^>]+>\s+)?"
            r"(\w+(?:<[^>]+>)?)\s+"
            r"(\w+)\s*\(",
            line,
        )
        if sig_match:
            method_name = sig_match.group(3)
            ann_start = i
            while ann_start > 0 and re.match(r"\s*@\w+", lines[ann_start - 1]):
                ann_start -= 1

            brace_line = i
            while brace_line < len(lines) and "{" not in lines[brace_line]:
                brace_line += 1
            if brace_line >= len(lines):
                i += 1
                continue

            depth = 0
            method_end = brace_line
            for j in range(brace_line, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth == 0:
                    method_end = j
                    break

            method_code = "\n".join(lines[ann_start:method_end + 1])
            methods.append({
                "name": method_name,
                "code": method_code,
                "start_line": ann_start + 1,
                "end_line": method_end + 1,
                "line_count": method_end - ann_start + 1,
            })
            i = method_end + 1
        else:
            i += 1
    return methods


def _find_ts_methods(code: str) -> list[dict]:
    lines = code.split("\n")
    methods = []
    method_pattern = re.compile(
        r"^\s*(?:async\s+)?"
        r"(?:public\s+|private\s+|protected\s+)?"
        r"(?:static\s+)?"
        r"(ngOnInit|ngOnDestroy|ngOnChanges|ngAfterViewInit|ngDoCheck|\w+)"
        r"\s*\([^)]*\)\s*(?::\s*\w+(?:<[^>]+>)?)?\s*\{",
    )
    i = 0
    while i < len(lines):
        match = method_pattern.match(lines[i])
        if match:
            method_name = match.group(1)
            if method_name == "constructor":
                depth = 0
                for j in range(i, len(lines)):
                    depth += lines[j].count("{") - lines[j].count("}")
                    if depth == 0:
                        i = j + 1
                        break
                else:
                    i += 1
                continue

            depth = 0
            method_end = i
            for j in range(i, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth == 0:
                    method_end = j
                    break

            method_code = "\n".join(lines[i:method_end + 1])
            methods.append({
                "name": method_name,
                "code": method_code,
                "start_line": i + 1,
                "end_line": method_end + 1,
                "line_count": method_end - i + 1,
            })
            i = method_end + 1
        else:
            i += 1
    return methods


# ---- Large Method Splitting -------------------------------------------------

def _split_large_method(method: dict, context_header: str, file_name: str, class_name: str, language: str) -> list[CodeChunk]:
    lines = method["code"].split("\n")
    sub_chunks = []
    current_start = 0
    part = 1

    for i in range(len(lines)):
        if i - current_start >= 60 and i < len(lines) - 5:
            split_at = i
            for j in range(i, min(i + 20, len(lines))):
                stripped = lines[j].strip()
                if stripped == "" or stripped == "}" or stripped.startswith("//"):
                    split_at = j + 1
                    break

            block_code = "\n".join(lines[current_start:split_at])
            sub_chunks.append(CodeChunk(
                file_name=file_name,
                language=language,
                class_name=class_name,
                class_context=context_header,
                method_name=f"{method['name']} (part {part})",
                code="",  # filled later with full context
                start_line=method["start_line"] + current_start,
                end_line=method["start_line"] + split_at,
                is_partial=True,
            ))
            # Store raw code temporarily
            sub_chunks[-1]._raw_code = block_code
            current_start = split_at
            part += 1

    if current_start < len(lines):
        block_code = "\n".join(lines[current_start:])
        sub_chunks.append(CodeChunk(
            file_name=file_name,
            language=language,
            class_name=class_name,
            class_context=context_header,
            method_name=f"{method['name']} (part {part})",
            code="",
            start_line=method["start_line"] + current_start,
            end_line=method["end_line"],
            is_partial=True,
        ))
        sub_chunks[-1]._raw_code = block_code

    for i, sc in enumerate(sub_chunks):
        sc.chunk_index = i
        sc.total_chunks = len(sub_chunks)

    return sub_chunks


# ---- Main Chunking Functions ------------------------------------------------

def chunk_java(code: str, file_name: str = "Unknown.java") -> tuple[list[CodeChunk], CallGraph]:
    ctx = _extract_java_class_context(code, file_name)
    call_graph = build_call_graph(code, "java", ctx["class_name"])
    context_header = _build_context_header(ctx, "java")
    call_graph_text = call_graph.format_for_prompt()

    methods = _find_java_methods(code)

    if not methods:
        full_code = context_header
        if call_graph_text:
            full_code += "\n" + call_graph_text
        full_code += f"\n\n[CODE UNDER REVIEW]\n{code}"
        return [CodeChunk(
            file_name=file_name, language="java",
            class_name=ctx["class_name"], class_context=context_header,
            method_name="(entire file)", code=full_code,
            start_line=1, end_line=len(code.split("\n")),
        )], call_graph

    chunks = []
    for i, method in enumerate(methods):
        if method["line_count"] > 80:
            sub_chunks = _split_large_method(
                method, context_header, file_name, ctx["class_name"], "java"
            )
            for sc in sub_chunks:
                full_code = context_header
                if call_graph_text:
                    full_code += "\n" + call_graph_text
                full_code += f"\n\n[METHOD UNDER REVIEW: {sc.method_name}]\n{sc._raw_code}"
                sc.code = full_code
            chunks.extend(sub_chunks)
        else:
            full_code = context_header
            if call_graph_text:
                full_code += "\n" + call_graph_text
            full_code += f"\n\n[METHOD UNDER REVIEW]\n{method['code']}"
            chunks.append(CodeChunk(
                file_name=file_name, language="java",
                class_name=ctx["class_name"], class_context=context_header,
                method_name=method["name"], code=full_code,
                start_line=method["start_line"], end_line=method["end_line"],
                chunk_index=i, total_chunks=len(methods),
            ))

    return chunks, call_graph


def chunk_typescript(code: str, file_name: str = "unknown.ts") -> tuple[list[CodeChunk], CallGraph]:
    ctx = _extract_ts_class_context(code, file_name)
    call_graph = build_call_graph(code, "typescript", ctx["class_name"])
    context_header = _build_context_header(ctx, "typescript")
    call_graph_text = call_graph.format_for_prompt()

    methods = _find_ts_methods(code)

    if not methods:
        full_code = context_header
        if call_graph_text:
            full_code += "\n" + call_graph_text
        full_code += f"\n\n[CODE UNDER REVIEW]\n{code}"
        return [CodeChunk(
            file_name=file_name, language="typescript",
            class_name=ctx["class_name"], class_context=context_header,
            method_name="(entire file)", code=full_code,
            start_line=1, end_line=len(code.split("\n")),
        )], call_graph

    chunks = []
    for i, method in enumerate(methods):
        if method["line_count"] > 80:
            sub_chunks = _split_large_method(
                method, context_header, file_name, ctx["class_name"], "typescript"
            )
            for sc in sub_chunks:
                full_code = context_header
                if call_graph_text:
                    full_code += "\n" + call_graph_text
                full_code += f"\n\n[METHOD UNDER REVIEW: {sc.method_name}]\n{sc._raw_code}"
                sc.code = full_code
            chunks.extend(sub_chunks)
        else:
            full_code = context_header
            if call_graph_text:
                full_code += "\n" + call_graph_text
            full_code += f"\n\n[METHOD UNDER REVIEW]\n{method['code']}"
            chunks.append(CodeChunk(
                file_name=file_name, language="typescript",
                class_name=ctx["class_name"], class_context=context_header,
                method_name=method["name"], code=full_code,
                start_line=method["start_line"], end_line=method["end_line"],
                chunk_index=i, total_chunks=len(methods),
            ))

    return chunks, call_graph


# ---- Public API -------------------------------------------------------------

def chunk_code(code: str, language: str, file_name: str = "") -> tuple[list[CodeChunk], CallGraph]:
    """Main entry point: chunk code by language. Returns (chunks, call_graph)."""
    if not file_name:
        file_name = "Unknown.java" if language == "java" else "unknown.ts"

    if language == "java":
        return chunk_java(code, file_name)
    elif language in ("typescript", "ts"):
        return chunk_typescript(code, file_name)
    else:
        cg = CallGraph(class_name=file_name, language=language)
        return [CodeChunk(
            file_name=file_name, language=language,
            class_name=file_name, class_context=f"[FILE CONTEXT]\nFile: {file_name}\n",
            method_name="(entire file)", code=code,
            start_line=1, end_line=len(code.split("\n")),
        )], cg
