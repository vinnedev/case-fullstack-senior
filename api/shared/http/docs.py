from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

STATIC_PATH = Path(__file__).resolve().parents[2] / "static"
FAVICON_PATH = STATIC_PATH / "favicon.png"
GALAXIES_LOGO_PATH = STATIC_PATH / "galaxies-logo.png"
FAVICON_URL = "/favicon.png"
GALAXIES_LOGO_URL = "/galaxies-logo.png"

SWAGGER_VERSION = "5.32.11"
SWAGGER_JS = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}/swagger-ui-bundle.js"
SWAGGER_JS_SRI = "sha384-vfl/klfTFrIz5urj0HnhcXLAbzPdRHezizfy+XgFB6GqcKkhlk0lS3bIbyB39NLA"

SWAGGER_CSS = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}/swagger-ui.css"
SWAGGER_CSS_SRI = "sha384-9Q2fpS+xeS4ffJy6CagnwoUl+4ldAYhOs9pgZuEKxypVModhmZFzeMlvVsAjf7uT"
FONTS_CSS = (
    "https://fonts.googleapis.com/css2"
    "?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700"
    "&family=Inter:wght@500;600;700"
    "&family=Fragment+Mono&display=swap"
)

THEME_CSS = """
:root {
  --brand: #0065ff;
  --brand-strong: #0051cc;
  --brand-soft: #e6f0ff;
  --brand-tint: #c7dcfe;
  --ink: #151515;
  --ink-soft: #70707a;
  --surface: #ffffff;
  --bg: #f5f6f8;
  --danger: #d92d20;
  --success: #34a853;
  --radius: 14px;
  --shadow: 0 1px 2px rgba(21, 21, 21, 0.05), 0 8px 24px rgba(21, 21, 21, 0.06);
}

html { min-height: 100%; }
body {
  margin: 0;
  min-height: 100vh;
  background-color: var(--bg);
  background-image: radial-gradient(1100px 520px at 85% -10%, var(--brand-soft) 0%, rgba(230, 240, 255, 0) 70%);
  background-repeat: no-repeat;
  background-attachment: fixed;
  -webkit-font-smoothing: antialiased;
}

.galaxies-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  max-width: 1140px;
  margin: 0 auto;
  padding: 28px 20px 6px;
  font-family: "DM Sans", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.galaxies-topbar .mark {
  display: flex;
  align-items: baseline;
  gap: 14px;
}
.galaxies-logo {
  height: 26px;
  width: auto;
  display: block;
}
.galaxies-topbar .by {
  color: var(--brand);
  font-weight: 600;
  font-size: 15px;
  letter-spacing: -0.01em;
}
.galaxies-topbar a {
  color: var(--ink-soft);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
}
.galaxies-topbar a:hover { color: var(--brand); }

.swagger-ui { max-width: 1140px; margin: 0 auto; }
.swagger-ui, .swagger-ui .info .title, .swagger-ui .opblock-tag,
.swagger-ui .opblock .opblock-summary-description, .swagger-ui table,
.swagger-ui .btn, .swagger-ui select, .swagger-ui input, .swagger-ui textarea,
.swagger-ui label, .swagger-ui .parameter__name, .swagger-ui .response-col_status {
  font-family: "DM Sans", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--ink);
}
.swagger-ui .info { margin: 24px 0; }
.swagger-ui .info .title { font-family: "Inter", "DM Sans", sans-serif; letter-spacing: -0.02em; }
.swagger-ui .info .title small.version-stamp { background: var(--brand); }
.swagger-ui .info .description p, .swagger-ui .info li, .swagger-ui .info td { color: var(--ink); }
.swagger-ui .info a { color: var(--brand); }
.swagger-ui .info .description h2 {
  font-family: "Inter", "DM Sans", sans-serif;
  letter-spacing: -0.01em;
  border-bottom: 1px solid rgba(21, 21, 21, 0.08);
  padding-bottom: 8px;
  margin-top: 36px;
}
.swagger-ui .info .description p,
.swagger-ui .info .description li {
  font-size: 15px;
  line-height: 1.9;
}
.swagger-ui .info .description li { margin: 6px 0; }
.swagger-ui .info .description code,
.swagger-ui .opblock-description-wrapper code,
.swagger-ui .markdown code,
.swagger-ui .renderedMarkdown code {
  font-family: "Fragment Mono", ui-monospace, monospace;
  background: var(--brand-soft);
  color: var(--brand-strong);
  font-size: 84%;
  line-height: 1;
  padding: 2px 7px;
  border-radius: 6px;
  white-space: nowrap;
  vertical-align: baseline;
}
.swagger-ui .info .description table { border-collapse: separate; border-spacing: 0; }
.swagger-ui .info .description table th,
.swagger-ui .info .description table td {
  padding: 12px 16px;
  line-height: 1.6;
  border-bottom: 1px solid rgba(21, 21, 21, 0.06);
}

.swagger-ui .scheme-container {
  background: var(--surface);
  border: 1px solid rgba(21, 21, 21, 0.06);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  margin: 0 0 20px;
  padding: 18px 0;
}
.swagger-ui .opblock-tag {
  font-family: "Inter", "DM Sans", sans-serif;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.01em;
  border-bottom: none;
  border-radius: var(--radius);
}
.swagger-ui .opblock-tag:hover { background: var(--brand-soft); }
.swagger-ui .opblock-tag small { color: var(--ink-soft); }

.swagger-ui .opblock {
  background: var(--surface);
  border: 1px solid rgba(21, 21, 21, 0.06);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  margin-bottom: 14px;
  overflow: hidden;
  transition: box-shadow 0.2s ease;
}
.swagger-ui .opblock:hover { box-shadow: 0 2px 4px rgba(21, 21, 21, 0.06), 0 12px 32px rgba(21, 21, 21, 0.1); }
.swagger-ui .opblock .opblock-summary {
  border: none;
  padding: 10px 14px;
  gap: 12px;
}
.swagger-ui .opblock .opblock-summary-method {
  border-radius: 8px;
  font-family: "DM Sans", sans-serif;
  font-weight: 600;
  font-size: 12px;
  letter-spacing: 0.04em;
  min-width: 76px;
  padding: 8px 0;
  box-shadow: none;
  text-shadow: none;
}
.swagger-ui .opblock .opblock-summary-path,
.swagger-ui .opblock .opblock-summary-path__deprecated {
  font-family: "Fragment Mono", ui-monospace, monospace;
  font-size: 14.5px;
}
.swagger-ui .opblock.opblock-get,
.swagger-ui .opblock.opblock-post {
  border-color: rgba(21, 21, 21, 0.06);
  background: var(--surface);
}
.swagger-ui .opblock.opblock-get .opblock-summary,
.swagger-ui .opblock.opblock-post .opblock-summary { border: none; }
.swagger-ui .opblock.opblock-get .opblock-summary-method { background: var(--brand); }
.swagger-ui .opblock.opblock-post .opblock-summary-method { background: var(--success); }
.swagger-ui .opblock.is-open .opblock-summary { border-bottom: 1px solid rgba(21, 21, 21, 0.06); }
.swagger-ui .opblock .opblock-section-header {
  background: var(--bg);
  box-shadow: none;
  border-radius: 9px;
}
.swagger-ui .opblock .opblock-section-header h4 { font-family: "Inter", "DM Sans", sans-serif; }

.swagger-ui .btn {
  border-radius: 10px;
  box-shadow: none;
  font-weight: 600;
}
.swagger-ui .btn.authorize {
  color: var(--brand);
  border-color: var(--brand);
}
.swagger-ui .btn.authorize svg { fill: var(--brand); }
.swagger-ui .btn.execute {
  background: var(--brand);
  border-color: var(--brand);
}
.swagger-ui .btn.execute:hover { background: var(--brand-strong); }
.swagger-ui .btn.cancel { color: var(--danger); border-color: var(--danger); }

.swagger-ui select, .swagger-ui input[type=text], .swagger-ui textarea {
  border-radius: 10px;
  border: 1px solid rgba(21, 21, 21, 0.12);
}
.swagger-ui input[type=text]:focus, .swagger-ui textarea:focus, .swagger-ui select:focus {
  outline: 2px solid var(--brand-tint);
  border-color: var(--brand);
}

.swagger-ui .model-box, .swagger-ui section.models .model-container {
  background: var(--bg);
  border-radius: 9px;
}
.swagger-ui section.models {
  border: 1px solid rgba(21, 21, 21, 0.06);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow);
}
.swagger-ui section.models h4 { font-family: "Inter", "DM Sans", sans-serif; }
.swagger-ui .model, .swagger-ui .prop-type { font-family: "Fragment Mono", ui-monospace, monospace; }
.swagger-ui .prop-type { color: var(--brand); }

.swagger-ui .responses-inner h4, .swagger-ui .responses-inner h5 {
  font-family: "Inter", "DM Sans", sans-serif;
}
.swagger-ui .response-col_status { font-family: "Fragment Mono", ui-monospace, monospace; }
.swagger-ui .microlight {
  font-family: "Fragment Mono", ui-monospace, monospace !important;
  border-radius: 9px;
}
.swagger-ui .copy-to-clipboard { background: var(--brand); border-radius: 7px; }
.swagger-ui .download-contents { background: var(--brand); border-radius: 7px; }

.swagger-ui .tab li button.tablinks { color: var(--ink-soft); }
.swagger-ui .tab li.active button.tablinks { color: var(--ink); }
.swagger-ui .opblock-summary-control:focus { outline-color: var(--brand); }
.swagger-ui .expand-methods svg, .swagger-ui .expand-operation svg { fill: var(--ink-soft); }
.swagger-ui .loading-container .loading::after { border-top-color: var(--brand); }
.swagger-ui .loading-container .loading::before { border-color: var(--brand-tint); }
"""


def install_branded_docs(app: FastAPI) -> None:
    swagger_params = {**(app.swagger_ui_parameters or {})}

    @app.get(FAVICON_URL, include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(FAVICON_PATH, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get(GALAXIES_LOGO_URL, include_in_schema=False)
    async def galaxies_logo() -> FileResponse:
        return FileResponse(GALAXIES_LOGO_PATH, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/docs", include_in_schema=False)
    async def branded_docs() -> HTMLResponse:
        params = "".join(f"        {key}: {_js(value)},\n" for key, value in swagger_params.items())
        html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{app.title} · Docs</title>
  <link rel="icon" type="image/png" href="{FAVICON_URL}">
  <link rel="apple-touch-icon" href="{FAVICON_URL}">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="{FONTS_CSS}">
  <link rel="stylesheet" href="{SWAGGER_CSS}" integrity="{SWAGGER_CSS_SRI}" crossorigin="anonymous">
  <style>{THEME_CSS}</style>
</head>
<body>
  <header class="galaxies-topbar">
    <div class="mark">
      <img class="galaxies-logo" src="{GALAXIES_LOGO_URL}" alt="Galaxies">
      <span class="by">· {app.title}</span>
    </div>
    <a href="https://www.galaxies.com.br" target="_blank" rel="noreferrer">galaxies.com.br</a>
  </header>
  <div id="swagger-ui"></div>
  <script src="{SWAGGER_JS}" integrity="{SWAGGER_JS_SRI}" crossorigin="anonymous"></script>
  <script>
    SwaggerUIBundle({{
        url: "{app.openapi_url}",
        dom_id: "#swagger-ui",
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset].filter(Boolean),
        layout: "BaseLayout",
        deepLinking: true,
{params}    }});
  </script>
</body>
</html>"""
        return HTMLResponse(html)


def _js(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{value}"'
