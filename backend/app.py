from pathlib import Path
import ast
import os
import re
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")

app = FastAPI(title="AI Codebase Understanding Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

state: dict[str, Any] = {"root": None, "files": [], "symbols": [], "edges": [], "documents": []}

IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
    "build", ".next", ".idea", ".vscode", "coverage"
}
EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cpp", ".c", ".h", ".hpp", ".go", ".rs"}


class AnalyzeRequest(BaseModel):
    path: str


class ChatRequest(BaseModel):
    question: str


def read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def python_symbols(source: str):
    symbols = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return symbols

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            symbols.append({
                "name": node.name,
                "kind": kind,
                "line": node.lineno,
            })
    return symbols


def generic_symbols(source: str):
    patterns = [
        ("class", r"\bclass\s+([A-Za-z_]\w*)"),
        ("function", r"(?:function\s+|(?:const|let|var)\s+)([A-Za-z_]\w*)\s*=?\s*(?:\([^)]*\)\s*=>|function)?"),
        ("function", r"\b(?:public|private|protected|static)?\s*(?:[\w<>\[\]]+\s+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"),
    ]
    out = []
    seen = set()
    for kind, pattern in patterns:
        for match in re.finditer(pattern, source):
            name = match.group(1)
            key = (kind, name)
            if key not in seen:
                seen.add(key)
                out.append({"name": name, "kind": kind, "line": source[:match.start()].count("\n") + 1})
    return out[:150]


def extract_symbols(path: Path, source: str):
    if path.suffix == ".py":
        return python_symbols(source)
    return generic_symbols(source)


def import_targets(source: str, path: Path, root: Path):
    targets = []
    if path.suffix == ".py":
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        targets.append(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    targets.append(node.module.split(".")[0])
        except SyntaxError:
            pass
    else:
        for match in re.finditer(r"""(?:import|from)\s+['"]([^'"]+)['"]""", source):
            targets.append(match.group(1))
        for match in re.finditer(r"""require\(\s*['"]([^'"]+)['"]\s*\)""", source):
            targets.append(match.group(1))
    return list(dict.fromkeys(targets))


def resolve_edge(source_file: Path, target: str, root: Path):
    if target.startswith("."):
        candidate = (source_file.parent / target).resolve()
        candidates = [
            candidate, *[candidate.with_suffix(ext) for ext in EXTENSIONS],
            candidate / "index.js", candidate / "index.ts", candidate / "__init__.py"
        ]
        for c in candidates:
            if c.is_file() and root in c.parents:
                return c.relative_to(root).as_posix()
    else:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in EXTENSIONS and p.stem == target and root in p.parents:
                return p.relative_to(root).as_posix()
    return None


def analyze_repo(root: Path):
    files_out, symbols_out, edges = [], [], []
    documents = []

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in EXTENSIONS:
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        source = read_source(path)
        if not source:
            continue

        syms = extract_symbols(path, source)
        files_out.append({"path": rel, "language": path.suffix.lstrip("."), "lines": source.count("\n") + 1})
        for s in syms:
            symbols_out.append({**s, "file": rel})

        for target in import_targets(source, path, root):
            resolved = resolve_edge(path, target, root)
            if resolved and resolved != rel:
                edges.append({"source": rel, "target": resolved, "label": target})

        documents.append({"path": rel, "text": source[:20000]})

    return files_out, symbols_out, list({(e["source"], e["target"]): e for e in edges}.values()), documents


def retrieve(question: str, documents, k=5):
    if not documents:
        return []
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    corpus = [d["path"] + "\n" + d["text"] for d in documents]
    vectorizer = TfidfVectorizer(stop_words="english", max_features=12000)
    matrix = vectorizer.fit_transform(corpus + [question])
    scores = cosine_similarity(matrix[-1], matrix[:-1]).flatten()
    ranked = scores.argsort()[::-1][:k]
    return [documents[i] for i in ranked if scores[i] > 0]


def ollama(prompt: str):
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": MODEL, "prompt": prompt, "stream": False},
            timeout=180,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not reach Ollama. Start Ollama and run `ollama pull {MODEL}`. Details: {exc}"
        )


@app.get("/api/health")
def health():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return {"ok": True, "ollama": r.ok, "model": MODEL}
    except requests.RequestException:
        return {"ok": True, "ollama": False, "model": MODEL}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    root = Path(req.path).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Project path does not exist or is not a directory.")

    files_out, symbols_out, edges, documents = analyze_repo(root)
    state.update({"root": str(root), "files": files_out, "symbols": symbols_out, "edges": edges, "documents": documents})

    summary = ollama(
        "You are a senior software engineer. Analyze this repository metadata and explain it "
        "to a student in simple language. Mention purpose, major modules, likely entry points, "
        "data/request flow, and how the pieces connect. Do not invent details.\n\n"
        f"Files:\n{files_out[:80]}\n\nSymbols:\n{symbols_out[:150]}\n\nEdges:\n{edges[:150]}"
    ) if files_out else "No supported source files were found."

    return {
        "root": str(root),
        "stats": {"files": len(files_out), "symbols": len(symbols_out), "relationships": len(edges)},
        "files": files_out,
        "symbols": symbols_out,
        "edges": edges,
        "summary": summary,
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not state["root"]:
        raise HTTPException(status_code=400, detail="Analyze a repository first.")

    context = retrieve(req.question, state["documents"])
    context_text = "\n\n".join(f"FILE: {d['path']}\n{d['text']}" for d in context)

    prompt = f"""You are a codebase understanding assistant.
Answer the user's question using only the repository context below.
Explain technical ideas in simple language. Name files and functions when useful.
If the context is insufficient, clearly say what cannot be determined.

USER QUESTION:
{req.question}

REPOSITORY CONTEXT:
{context_text}
"""
    return {"answer": ollama(prompt), "sources": [d["path"] for d in context]}


@app.get("/api/graph")
def graph():
    if not state["root"]:
        raise HTTPException(status_code=400, detail="Analyze a repository first.")
    return {"nodes": state["files"], "edges": state["edges"], "symbols": state["symbols"]}
