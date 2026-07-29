# Relay Web

SPA React que permite submeter, listar, consultar, cancelar e reenfileirar
jobs. A interface também expõe a visão administrativa para o contexto `admin`.

## Executar localmente

```bash
cp .env.example .env
npm ci
npm run dev
```

O frontend lê somente `web/.env`; defina `VITE_API_URL` para a URL da API.

## Validar

```bash
npm test
npx tsc --noEmit
npm run build
```

Fluxos de interface, tipagem e integração estão documentados em
[`../docs/web.md`](../docs/web.md).
