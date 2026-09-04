import "@/App.css";
import { Toaster } from "@/components/ui/sonner";
import Dashboard from "@/components/Dashboard";

function App() {
  return (
    <div className="App">
      <Dashboard />
      <Toaster position="bottom-right" theme="dark" richColors />
    </div>
  );
}

export default App;
