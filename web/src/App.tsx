import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FAKE_USERS, roleOf } from "./auth";
import { AdminPanel } from "./components/organisms/AdminPanel";
import { Header } from "./components/organisms/Header";
import { JobsPanel } from "./components/organisms/JobsPanel";
import { ToastProvider } from "./toast";

const queryClient = new QueryClient();

export default function App() {
  const [auth, setAuth] = useState<string>(FAKE_USERS[0]);

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <div className="shell">
          <Header auth={auth} onAuthChange={setAuth} />
          <JobsPanel auth={auth} />
          {roleOf(auth) === "admin" && <AdminPanel auth={auth} />}
        </div>
      </ToastProvider>
    </QueryClientProvider>
  );
}
