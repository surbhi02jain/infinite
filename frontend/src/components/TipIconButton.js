import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";

export default function TipIconButton({ label, side = "bottom", children, className = "", ...props }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button size="icon" variant="ghost" className={`h-7 w-7 ${className}`} {...props}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side={side} className="font-mono text-[11px]">{label}</TooltipContent>
    </Tooltip>
  );
}
