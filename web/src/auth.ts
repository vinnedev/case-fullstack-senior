// 4 "usuários" fake para trocar via dropdown. Formato do header: "<company_id>:<role>".
export const FAKE_USERS = ["1:user", "1:admin", "2:user", "2:admin"] as const;

const COMPANY_NAMES: Record<string, string> = { "1": "Acme", "2": "Globex" };
const ROLE_NAMES: Record<string, string> = { user: "usuário", admin: "admin" };

export function roleOf(auth: string): string {
  return auth.split(":")[1] ?? "";
}

export function labelOf(auth: string): string {
  const [company, role] = auth.split(":");
  return `${COMPANY_NAMES[company] ?? `Empresa ${company}`} · ${ROLE_NAMES[role] ?? role}`;
}
