import { useState } from "react";
import LoginForm from "./components/LoginForm";
import Dashboard from "./components/Dashboard";

export default function App() {
  const [session, setSession] = useState(() => {
    const raw = localStorage.getItem("dnu_session");
    return raw ? JSON.parse(raw) : null;
  });

  function handleLogin(res) {
    setSession(res);
    localStorage.setItem("dnu_session", JSON.stringify(res));
  }

  function handleLogout() {
    setSession(null);
    localStorage.removeItem("dnu_session");
  }

  return session
    ? <Dashboard session={session} onLogout={handleLogout} />
    : <LoginForm onLogin={handleLogin} />;
}
