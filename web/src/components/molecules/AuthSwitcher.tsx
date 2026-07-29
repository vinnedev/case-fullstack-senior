import { FAKE_USERS, labelOf } from "../../auth";
import { Select } from "../atoms/Select";

export function AuthSwitcher({ auth, onChange }: { auth: string; onChange: (auth: string) => void }) {
  return (
    <Select label="Entrar como" value={auth} onChange={(e) => onChange(e.target.value)}>
      {FAKE_USERS.map((u) => (
        <option key={u} value={u}>
          {labelOf(u)}
        </option>
      ))}
    </Select>
  );
}
