import { useState } from "react";
import { Globe, Lock, FileText, Sparkles, Rocket, ShoppingCart, CreditCard, Bot, LayoutDashboard, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

const PRESETS = [
  { id: "ecom", name: "E-Commerce Checkout", icon: ShoppingCart, url: "https://demo.playwright.dev/todomvc",
    intent: "Add items to cart, apply coupon, complete checkout, verify order confirmation",
    prd: "Users must be able to browse products, add to cart, apply discount codes, and checkout. Empty cart checkout must be blocked. Payment failure must show a clear error." },
  { id: "saas", name: "SaaS Auth & Billing", icon: CreditCard, url: "https://the-internet.herokuapp.com/login",
    intent: "Sign in, verify dashboard, test invalid credentials, upgrade plan",
    prd: "Multi-tenant login with email/password. Invalid login shows error. Locked accounts blocked. Billing upgrade updates plan tier." },
  { id: "chat", name: "AI Assistant Chat", icon: Bot, url: "https://example.com",
    intent: "Send a message, verify streaming response, test empty message handling",
    prd: "Chat interface streams AI responses. Empty messages rejected. Long messages truncated. History persists across reload." },
  { id: "fintech", name: "Fintech Dashboard", icon: LayoutDashboard, url: "https://the-internet.herokuapp.com",
    intent: "View balance, initiate transfer, verify insufficient funds error",
    prd: "Dashboard shows account balance. Transfers validate sufficient funds. Negative amounts blocked. 2FA on large transfers." },
];

// Only Sarvam AI model ids verified to actually respond (200) from api.sarvam.ai — others in this
// account return 400 Bad Request on every call, which would silently degrade that agent to its
// deterministic fallback for the whole run. Re-verify before adding one back.
const MODELS = ["sarvam-105b", "sarvam-105b-conversations"];
const AGENTS = [["planner", "Planner"], ["evaluator", "Evaluator"], ["generator", "Generator"], ["healer", "Healer"]];

export default function RunForm({ onSubmit }) {
  const [url, setUrl] = useState("");
  const [loginUrl, setLoginUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [prd, setPrd] = useState("");
  const [intent, setIntent] = useState("");
  const [budget, setBudget] = useState("standard");
  const [workers, setWorkers] = useState("3");
  const [pause, setPause] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [models, setModels] = useState({ planner: "sarvam-105b", evaluator: "sarvam-105b", generator: "sarvam-105b", healer: "sarvam-105b" });

  const applyPreset = (p) => { setUrl(p.url); setIntent(p.intent); setPrd(p.prd); };

  const submit = () => {
    onSubmit({ url, login_url: loginUrl || null, username: username || null, password: password || null,
      prd: prd || null, intent: intent || null, budget, workers: parseInt(workers), pause_after_plan: pause, models });
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-10">
        <div className="mb-8">
          <div className="font-mono text-[11px] text-emerald-400 tracking-widest uppercase mb-2">Launch Autonomous Run</div>
          <h2 className="font-heading text-3xl lg:text-4xl font-bold tracking-tight text-white">
            Point the meta-agent at any web app.
          </h2>
          <p className="text-slate-400 mt-3 text-sm max-w-xl leading-relaxed">
            Paste a URL. The pipeline explores the DOM, plans meaningful flows, audits coverage gaps,
            generates Playwright specs with live selector validation, runs them, and self-heals — streaming every decision.
          </p>
        </div>

        {/* presets */}
        <div className="mb-8">
          <Label className="text-xs text-slate-400 font-mono uppercase tracking-widest">Quick presets</Label>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
            {PRESETS.map((p) => {
              const Icon = p.icon;
              return (
                <button key={p.id} data-testid={`preset-${p.id}`} onClick={() => applyPreset(p)}
                  className="group text-left p-3 rounded-xl border border-slate-800 bg-[#0d131f] hover:border-emerald-500/50 hover:bg-[#101827] transition-colors duration-200">
                  <Icon className="w-5 h-5 text-emerald-400 mb-2" />
                  <div className="text-[13px] font-medium text-slate-200 leading-tight">{p.name}</div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="space-y-5 rounded-2xl border border-slate-800 bg-[#0b101c] p-6">
          <div>
            <Label className="flex items-center gap-2 text-slate-300 mb-2"><Globe className="w-4 h-4 text-cyan-400" /> Target URL <span className="text-rose-400">*</span></Label>
            <Input data-testid="run-form-url-input" value={url} onChange={(e) => setUrl(e.target.value)}
              placeholder="https://your-app.com" className="bg-[#07090e] border-slate-700 font-mono text-sm h-11" />
          </div>

          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <Label className="flex items-center gap-2 text-slate-300 mb-2"><Lock className="w-4 h-4 text-amber-400" /> Login URL <span className="text-slate-600 text-xs">(optional)</span></Label>
              <Input data-testid="run-form-login-url-input" value={loginUrl} onChange={(e) => setLoginUrl(e.target.value)}
                placeholder="https://your-app.com/login" className="bg-[#07090e] border-slate-700 font-mono text-sm h-11" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-slate-300 mb-2 block text-sm">Username</Label>
                <Input data-testid="run-form-username-input" value={username} onChange={(e) => setUsername(e.target.value)}
                  placeholder="test@user.com" className="bg-[#07090e] border-slate-700 text-sm h-11" />
              </div>
              <div>
                <Label className="text-slate-300 mb-2 block text-sm">Password</Label>
                <Input data-testid="run-form-password-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••" className="bg-[#07090e] border-slate-700 text-sm h-11" />
              </div>
            </div>
          </div>
          <p className="text-[11px] text-slate-500 -mt-2">
            Credentials ⇒ agent logs in once, persists Playwright <span className="font-mono text-slate-400">storageState</span> and reuses it. No creds ⇒ public-flow mode.
          </p>

          <div>
            <Label className="flex items-center gap-2 text-slate-300 mb-2"><FileText className="w-4 h-4 text-violet-400" /> PRD <span className="text-slate-600 text-xs">(optional — steers planner + gap analysis)</span></Label>
            <Textarea data-testid="run-form-prd-textarea" value={prd} onChange={(e) => setPrd(e.target.value)} rows={3}
              placeholder="Paste product requirements. The evaluator flags PRD requirements the plan misses." className="bg-[#07090e] border-slate-700 text-sm resize-none" />
          </div>

          <div>
            <Label className="flex items-center gap-2 text-slate-300 mb-2"><Sparkles className="w-4 h-4 text-fuchsia-400" /> Natural-language test intent <span className="text-slate-600 text-xs">(optional)</span></Label>
            <Input data-testid="run-form-intent-input" value={intent} onChange={(e) => setIntent(e.target.value)}
              placeholder="e.g. focus on checkout and error handling on payment failure" className="bg-[#07090e] border-slate-700 text-sm h-11" />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <Label className="text-slate-300 mb-2 block text-sm">Run budget</Label>
              <Select value={budget} onValueChange={setBudget}>
                <SelectTrigger data-testid="run-form-budget-select" className="bg-[#07090e] border-slate-700 h-11"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-[#0d131f] border-slate-700 text-slate-200">
                  <SelectItem value="quick">Quick (fast, fewer flows)</SelectItem>
                  <SelectItem value="standard">Standard</SelectItem>
                  <SelectItem value="thorough">Thorough (max coverage)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-slate-300 mb-2 block text-sm">Parallel workers</Label>
              <Select value={workers} onValueChange={setWorkers}>
                <SelectTrigger data-testid="run-form-workers-select" className="bg-[#07090e] border-slate-700 h-11"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-[#0d131f] border-slate-700 text-slate-200">
                  {["1", "2", "3", "4", "6"].map((w) => <SelectItem key={w} value={w}>{w} worker{w !== "1" ? "s" : ""}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#07090e] px-3 h-11 mt-auto">
              <span className="text-sm text-slate-300">Pause for plan approval</span>
              <Switch data-testid="run-form-pause-switch" checked={pause} onCheckedChange={setPause} />
            </div>
          </div>

          <button onClick={() => setShowAdvanced(!showAdvanced)} data-testid="toggle-advanced-button"
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 font-mono transition-colors">
            <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAdvanced ? "rotate-180" : ""}`} /> Per-agent model config
          </button>
          {showAdvanced && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 animate-fadein">
              {AGENTS.map(([key, label]) => (
                <div key={key}>
                  <Label className="text-[11px] text-slate-400 mb-1.5 block font-mono uppercase tracking-wider">{label}</Label>
                  <Select value={models[key]} onValueChange={(v) => setModels((m) => ({ ...m, [key]: v }))}>
                    <SelectTrigger data-testid={`model-select-${key}`} className="bg-[#07090e] border-slate-700 h-9 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent className="bg-[#0d131f] border-slate-700 text-slate-200">
                      {MODELS.map((mo) => <SelectItem key={mo} value={mo} className="text-xs font-mono">{mo}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </div>
          )}

          <Button data-testid="run-form-submit-button" onClick={submit} disabled={!url}
            className="w-full h-12 bg-emerald-500 hover:bg-emerald-400 text-[#07090e] font-bold text-base rounded-xl shadow-[0_0_30px_rgba(16,185,129,0.3)] disabled:opacity-40 disabled:shadow-none">
            <Rocket className="w-5 h-5 mr-2" /> Run Autonomous Pipeline
          </Button>
        </div>
      </div>
    </div>
  );
}
