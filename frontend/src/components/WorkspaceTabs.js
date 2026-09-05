import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ListChecks, ShieldAlert, Code2, PlayCircle, Wrench, FileBarChart } from "lucide-react";
import TestPlanView from "@/components/TestPlanView";
import PlanEvaluation from "@/components/PlanEvaluation";
import CodeViewer from "@/components/CodeViewer";
import ExecutionFeed from "@/components/ExecutionFeed";
import HealerLog from "@/components/HealerLog";
import FinalReport from "@/components/FinalReport";

export default function WorkspaceTabs({ derived, runId, run, activeTab, onTabChange, onEventAppend, highlightFlowId, onViewEvidence }) {
  const badge = (n) => n > 0 ? <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-slate-700/60 text-[9px] font-mono">{n}</span> : null;
  const reviewCount = derived.needsReview?.length || 0;
  return (
    <Tabs value={activeTab} onValueChange={onTabChange} className="flex-1 flex flex-col overflow-hidden">
      <TabsList className="h-11 justify-start bg-[#0b101c] border-b border-slate-800/80 rounded-none px-3 gap-1 shrink-0 w-full">
        <TabsTrigger data-testid="test-plan-tab-trigger" value="plan" className="data-[state=active]:bg-slate-800 data-[state=active]:text-white text-slate-400 text-xs">
          <ListChecks className="w-3.5 h-3.5 mr-1.5" /> Plan {badge(derived.flows.length)}
        </TabsTrigger>
        <TabsTrigger data-testid="plan-evaluation-tab-trigger" value="eval" className="data-[state=active]:bg-slate-800 data-[state=active]:text-white text-slate-400 text-xs">
          <ShieldAlert className="w-3.5 h-3.5 mr-1.5" /> Audit {badge(derived.gaps.length)}
        </TabsTrigger>
        <TabsTrigger data-testid="generated-code-tab-trigger" value="code" className="data-[state=active]:bg-slate-800 data-[state=active]:text-white text-slate-400 text-xs">
          <Code2 className="w-3.5 h-3.5 mr-1.5" /> Code {badge(derived.specs.length)}
        </TabsTrigger>
        <TabsTrigger data-testid="execution-feed-tab-trigger" value="exec" className="data-[state=active]:bg-slate-800 data-[state=active]:text-white text-slate-400 text-xs">
          <PlayCircle className="w-3.5 h-3.5 mr-1.5" /> Runner {badge(derived.executions.length)}
        </TabsTrigger>
        <TabsTrigger data-testid="healer-log-tab-trigger" value="heal" className="data-[state=active]:bg-slate-800 data-[state=active]:text-white text-slate-400 text-xs">
          <Wrench className="w-3.5 h-3.5 mr-1.5" /> Healer
          {reviewCount > 0 ? (
            <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 text-[9px] font-mono animate-pulse">
              {reviewCount} review
            </span>
          ) : badge(derived.healer.length)}
        </TabsTrigger>
        <TabsTrigger data-testid="final-report-tab-trigger" value="report" className="data-[state=active]:bg-slate-800 data-[state=active]:text-white text-slate-400 text-xs">
          <FileBarChart className="w-3.5 h-3.5 mr-1.5" /> Report
        </TabsTrigger>
      </TabsList>

      <div className="flex-1 overflow-y-auto">
        <TabsContent value="plan" className="mt-0 p-4 lg:p-6"><TestPlanView flows={derived.flows} /></TabsContent>
        <TabsContent value="eval" className="mt-0 p-4 lg:p-6"><PlanEvaluation gaps={derived.gaps} prdGaps={derived.prdGaps} riskNotes={derived.riskNotes} /></TabsContent>
        <TabsContent value="code" className="mt-0 p-0 h-full"><CodeViewer specs={derived.specs} runId={runId} run={run} /></TabsContent>
        <TabsContent value="exec" className="mt-0 p-4 lg:p-6"><ExecutionFeed executions={derived.executions} highlightFlowId={highlightFlowId} /></TabsContent>
        <TabsContent value="heal" className="mt-0 p-4 lg:p-6"><HealerLog healer={derived.healer} runId={runId} onEventAppend={onEventAppend} onViewEvidence={onViewEvidence} /></TabsContent>
        <TabsContent value="report" className="mt-0 p-4 lg:p-6"><FinalReport report={derived.report} runId={runId} run={run} onViewEvidence={onViewEvidence} /></TabsContent>
      </div>
    </Tabs>
  );
}
