import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import mermaid from "mermaid";
import "./style.css";

const API = "http://localhost:8000/api";

function Diagram({ edges }) {
  const ref = useRef(null);
  useEffect(() => {
    mermaid.initialize({ startOnLoad: false, securityLevel: "loose", theme: "dark" });
    const safe = (s) => s.replace(/[^a-zA-Z0-9_]/g, "_");
    const nodes = [...new Set(edges.flatMap(e => [e.source, e.target]))];
    const body = [
      "graph LR",
      ...nodes.map(n => `  ${safe(n)}["${n}"]`),
      ...edges.map(e => `  ${safe(e.source)} -->|imports| ${safe(e.target)}`)
    ].join("\n");
    mermaid.render("repoGraph", body).then(({ svg }) => { if (ref.current) ref.current.innerHTML = svg; });
  }, [edges]);
  return <div className="diagram" ref={ref} />;
}

function App() {
  const [path, setPath] = useState("");
  const [data, setData] = useState(null);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function analyze() {
    setLoading(true); setError("");
    try {
      const r = await fetch(`${API}/analyze`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path}) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Analysis failed");
      setData(j);
      setMessages([{role:"assistant", text:j.summary}]);
    } catch(e) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function ask() {
    if (!question.trim()) return;
    const q = question; setQuestion("");
    setMessages(m => [...m, {role:"user", text:q}]);
    setLoading(true); setError("");
    try {
      const r = await fetch(`${API}/chat`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({question:q}) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Chat failed");
      setMessages(m => [...m, {role:"assistant", text:j.answer, sources:j.sources}]);
    } catch(e) { setError(e.message); }
    finally { setLoading(false); }
  }

  return <div className="app">
    <header>
      <div><div className="eyebrow">LOCAL AI • CODE INTELLIGENCE</div><h1>Codebase Understanding Assistant</h1><p>Turn complex repositories into simple explanations, relationships and architecture.</p></div>
      <div className="badge">OLLAMA / FREE</div>
    </header>

    <section className="control">
      <input value={path} onChange={e=>setPath(e.target.value)} placeholder="Absolute project path, e.g. C:\\Users\\You\\Desktop\\my-project" />
      <button onClick={analyze} disabled={loading || !path}>{loading ? "Analyzing..." : "Analyze Repository"}</button>
    </section>

    {error && <div className="error">{error}</div>}

    {!data ? <section className="hero"><h2>Understand any supported codebase locally.</h2><p>No paid API. Your source code stays on your machine and is processed by your local Ollama model.</p><div className="cards"><span>📁 File map</span><span>🔗 Relationships</span><span>🧠 AI explanations</span><span>🗺️ Architecture graph</span></div></section> :
    <main>
      <div className="stats">
        <div><b>{data.stats.files}</b><span>Files</span></div>
        <div><b>{data.stats.symbols}</b><span>Classes / Functions</span></div>
        <div><b>{data.stats.relationships}</b><span>Relationships</span></div>
      </div>

      <section className="grid">
        <div className="panel"><h2>Project Understanding</h2><div className="markdown">{data.summary}</div></div>
        <div className="panel"><h2>Repository Structure</h2><div className="filelist">{data.files.map(f=><div key={f.path}><b>{f.path}</b><small>{f.language} • {f.lines} lines</small></div>)}</div></div>
      </section>

      <section className="panel"><h2>Architecture / File Relationships</h2><Diagram edges={data.edges}/></section>

      <section className="panel chat"><h2>Ask Your Codebase</h2>
        <div className="messages">{messages.map((m,i)=><div key={i} className={m.role}><div>{m.text}</div>{m.sources && <small>Sources: {m.sources.join(", ")}</small>}</div>)}</div>
        <div className="ask"><input value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>e.key==="Enter"&&ask()} placeholder="e.g. How does authentication work?" /><button onClick={ask} disabled={loading}>Ask</button></div>
      </section>

      <section className="panel"><h2>Classes & Functions</h2><div className="symbols">{data.symbols.map((s,i)=><div key={i}><span>{s.kind}</span><b>{s.name}</b><small>{s.file}:{s.line}</small></div>)}</div></section>
    </main>}
  </div>
}

createRoot(document.getElementById("root")).render(<App />);
