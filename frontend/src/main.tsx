import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { ToastProvider } from "./components/Toast";
import { LocaleProvider } from "./lib/i18n";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* 语言和时区在最外层：换语言要让整棵树重渲染一次，
            所以它必须比所有会显示文案和时间的东西都靠外 */}
        <LocaleProvider>
          {/* 提示挂在最外面：下载、保存这些反馈跟着页面走的话，
              切页面时提示会跟着一起被卸载 */}
          <ToastProvider>
            <App />
          </ToastProvider>
        </LocaleProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
