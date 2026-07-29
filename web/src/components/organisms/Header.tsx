import { Logo } from "../atoms/Logo";
import { AuthSwitcher } from "../molecules/AuthSwitcher";

export function Header({ auth, onAuthChange }: { auth: string; onAuthChange: (auth: string) => void }) {
  return (
    <header className="topbar">
      <Logo />
      <AuthSwitcher auth={auth} onChange={onAuthChange} />
    </header>
  );
}
