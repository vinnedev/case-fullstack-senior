import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, post } from "../../api";
import { useToast } from "../../toast";
import { parseJobCreated } from "../../types";
import { Button } from "../atoms/Button";

export function SubmitButton({ auth, onCreated }: { auth: string; onCreated?: (jobId: number) => void }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const idempotencyKey = useRef<string>(crypto.randomUUID());
  const [pending, setPending] = useState(false);

  async function submit() {
    setPending(true);
    try {
      const job = await post(
        "/jobs",
        auth,
        parseJobCreated,
        { kind: "report" },
        { "Idempotency-Key": idempotencyKey.current },
      );
      idempotencyKey.current = crypto.randomUUID();
      toast("success", `Job #${job.id} criado`, "Na fila para processamento.");
      onCreated?.(job.id);
    } catch (e) {
      toast("error", "Não foi possível criar o job", e instanceof ApiError ? e.message : "erro inesperado");
    } finally {
      setPending(false);
      queryClient.invalidateQueries({ queryKey: ["jobs", auth] });
    }
  }

  return (
    <Button variant="primary" onClick={submit} disabled={pending}>
      {pending ? "Enviando…" : "Novo job"}
    </Button>
  );
}
