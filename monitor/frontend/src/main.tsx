import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "@/App";
import { ThemeProvider } from "@/hooks/use-theme";
import { TimezoneProvider } from "@/hooks/use-timezone";
import { MonitorDataProvider } from "@/hooks/use-monitor-data";
import { TooltipProvider } from "@/components/ui/tooltip";
import "@/index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root is missing");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <ThemeProvider>
      <TimezoneProvider>
        <MonitorDataProvider>
          <TooltipProvider delayDuration={150}>
            <HashRouter>
              <App />
            </HashRouter>
          </TooltipProvider>
        </MonitorDataProvider>
      </TimezoneProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
