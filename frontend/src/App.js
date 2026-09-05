import "@/App.css";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import Dashboard from "@/components/Dashboard";

function App() {
  return (
    <TooltipProvider delayDuration={300}>
      <div className="App">
        <Dashboard />
        <Toaster position="bottom-right" theme="dark" richColors />
      </div>
    </TooltipProvider>
  );
}

export default App;
