import { useState, useEffect } from "react";
import { Copy, CheckCircle2, FileCode, ShieldCheck, ShieldAlert, Download, Info, Wrench, ArrowRight } from "lucide-react";
import { toast } from "sonner";
import { Empty } from "@/components/TestPlanView";
import { ARTIFACT_BASE } from "@/api";

const TYPE_DOT = { happy: "bg-emerald-400", edge: "bg-cyan-400", error: "bg-rose-400" };

export default function CodeViewer({ specs, runId, run }) {
  const [active, setActive] = useState(0);
  useEffect(() => { if (active >= specs.length) setActive(0); }, [specs.length, active]);
  if (!specs.length) return <Empty text="Generated Playwright specs will appear here…" />;
  const spec = specs[Math.min(active, specs.length - 1)];
  const needsStorageState = /storageState/.test(spec.code || "");
  const storageStateUrl = runId ? `${ARTIFACT_BASE}/artifacts/${runId}/storageState.json` : null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(spec.code);
      toast.success("Spec copied to clipboard");
    } catch (_) {
      // clipboard API blocked (permissions/non-secure) — fallback
      try {
        const ta = document.createElement("textarea");
        ta.value = spec.code;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        toast.success("Spec copied to clipboard");
      } catch (err) {
        toast.error("Copy failed — select the code manually");
      }
    }
  };

  return (
    <div className="flex h-full">
      {/* file list */}
      <div className="w-64 shrink-0 border-r border-slate-800/80 bg-[#0a0f18] overflow-y-auto">
        <div className="px-3 py-2.5 font-mono text-[10px] uppercase tracking-widest text-slate-500 border-b border-slate-800/80">
          Test Specs ({specs.length})
        </div>
        {specs.map((s, i) => {
          const verified = s.selectors.filter((x) => x.status === "verified").length;
          return (
            <button key={s.id} data-testid={`spec-file-${i}`} onClick={() => setActive(i)}
              className={`w-full text-left px-3 py-2.5 border-b border-slate-800/50 transition-colors duration-150 ${
                i === active ? "bg-[#101a28]" : "hover:bg-[#0d131f]"}`}>
              <div className="flex items-center gap-2">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${TYPE_DOT[s.flow_type] || "bg-slate-500"}`} />
                <FileCode className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                <span className="font-mono text-[11px] text-slate-300 truncate">{s.filename}</span>
                {s.healed_selectors?.length > 0 && <Wrench className="w-3 h-3 text-amber-400 shrink-0" />}
              </div>
              <div className="mt-1 ml-5 flex items-center gap-1 font-mono text-[9px]">
                {verified === s.selectors.length ? (
                  <span className="text-emerald-400 flex items-center gap-0.5"><ShieldCheck className="w-2.5 h-2.5" />{verified}/{s.selectors.length} verified</span>
                ) : (
                  <span className="text-amber-400 flex items-center gap-0.5"><ShieldAlert className="w-2.5 h-2.5" />{verified}/{s.selectors.length} verified</span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* code */}
      <div className="flex-1 flex flex-col overflow-hidden bg-[#07090e]">
        <div className="h-10 px-4 flex items-center justify-between border-b border-slate-800/80 shrink-0">
          <span className="font-mono text-xs text-slate-400">{spec.filename}</span>
          <button data-testid="code-copy-button" onClick={copy}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-emerald-400 transition-colors font-mono">
            <Copy className="w-3.5 h-3.5" /> Copy
          </button>
        </div>
        <div className="shrink-0 border-b border-slate-800/80 bg-[#0d131f] px-4 py-2 flex flex-col gap-1.5">
          <div className="flex items-start gap-2 text-[11px] text-slate-500">
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>Portable starting point for your own suite — the <b className="text-slate-400">Runner</b> tab shows the
              actual locators, screenshots and pass/fail from the real live-browser execution, which drives its own
              interpreter rather than this file.</span>
          </div>
          {spec.healed_selectors?.length > 0 && (
            <div className="flex flex-col gap-1" data-testid="spec-healed-diff">
              {spec.healed_selectors.map((h, i) => (
                <div key={i} className="flex items-center gap-1.5 text-[11px] text-amber-300">
                  <Wrench className="w-3 h-3 shrink-0" />
                  <span>Self-healed{h.confidence != null ? ` (conf ${Math.round(h.confidence * 100)}%)` : ""}:</span>
                  <code className="text-slate-400 line-through">{h.old_selector || "stale locator"}</code>
                  <ArrowRight className="w-3 h-3 shrink-0" />
                  <code className="text-emerald-300">{h.new_selector}</code>
                </div>
              ))}
            </div>
          )}
          {needsStorageState && (
            <a href={storageStateUrl} download="storageState.json" data-testid="download-storage-state-link"
              className="flex items-center gap-1.5 text-[11px] text-amber-300 hover:text-amber-200 w-fit">
              <Download className="w-3 h-3" /> This spec references storageState.json — download it here to run standalone
            </a>
          )}
        </div>
        <div className="flex-1 overflow-auto">
          <pre data-testid="code-content" className="p-4 font-mono text-[12px] leading-relaxed"><code dangerouslySetInnerHTML={{ __html: highlight(spec.code) }} /></pre>
        </div>
        {/* selector validation footer */}
        <div className="shrink-0 border-t border-slate-800/80 bg-[#0a0f18] p-3 max-h-40 overflow-y-auto">
          <div className="font-mono text-[10px] uppercase tracking-widest text-slate-500 mb-2">Live Selector Validation</div>
          <div className="flex flex-wrap gap-1.5">
            {spec.selectors.map((s, i) => (
              <span key={i} className={`inline-flex items-center gap-1 px-2 py-1 rounded border font-mono text-[10px] ${
                s.status === "verified" ? "border-emerald-500/30 bg-emerald-950/40 text-emerald-300" : "border-amber-500/30 bg-amber-950/40 text-amber-300"}`}>
                {s.status === "verified" ? <CheckCircle2 className="w-2.5 h-2.5" /> : <ShieldAlert className="w-2.5 h-2.5" />}
                {s.selector}
              </span>
            ))}
            {spec.selectors.length === 0 && <span className="text-slate-600 font-mono text-[11px]">No explicit selectors</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

function highlight(code) {
  if (!code) return "";
  const esc = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // single left-to-right pass so injected markup is never re-processed
  const pattern = /(\/\/[^\n]*)|('[^']*'|`[^`]*`|"[^"]*")|\b(import|from|const|let|var|await|async|test|expect|function|return|if|else|for|of)\b|\b(page|browser|context|describe|beforeEach|beforeAll)\b|(\.\w+)(?=\s*\()/g;
  return esc.replace(pattern, (m, cm, str, kw, obj, fn) => {
    if (cm) return `<span class="tok-cm">${cm}</span>`;
    if (str) return `<span class="tok-str">${str}</span>`;
    if (kw) return `<span class="tok-kw">${kw}</span>`;
    if (obj) return `<span class="tok-obj">${obj}</span>`;
    if (fn) return `<span class="tok-fn">${fn}</span>`;
    return m;
  });
}
