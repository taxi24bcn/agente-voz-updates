from __future__ import annotations
from pathlib import Path

MANAGED_DEFAULTS: dict[str, str] = {
    "CLOUD_WEBHOOK_URL": "https://script.google.com/macros/s/AKfycbxQKWR_EV4t7UulGOqi1WCg1BTyzmiYppyiNtmNRY6vE_kEXdPrjotMVGF_BfhahUW1/exec",
    "CLOUD_WEBHOOK_TOKEN": "tk_bb63e34505ddd2f0b234d61e4b9429c915b551e1",
}

_EMPTY_QUOTED = {'""', "''"}


def ensure_local_env_defaults(env_path: Path) -> bool:
    """
    Garantiza que env_path contiene las claves gestionadas.
    - Respeta el último valor no vacío del usuario (gana la última ocurrencia).
    - Trata "" y '' como vacíos — los rellena con el default.
    - Añade/corrige solo las claves que faltan o están vacías.
    - Elimina duplicados de claves gestionadas.
    - Escritura atómica: escribe a .env.tmp y luego replace().
    Devuelve True si se modificó el archivo, False si ya estaba correcto.
    Puede lanzar excepciones de filesystem; el caller decide si degradar en fail-soft.
    """
    env_path.parent.mkdir(parents=True, exist_ok=True)

    original_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = original_text.splitlines()

    # Última ocurrencia no vacía de cada clave gestionada
    existing_values: dict[str, str] = {}
    preserved_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            preserved_lines.append(line)
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in MANAGED_DEFAULTS:
            val = value.strip()
            if val in _EMPTY_QUOTED:  # "" y '' se tratan como vacío
                val = ""
            if val:
                existing_values[key] = val  # gana la última ocurrencia válida
            # no preservamos: se reescribe al final limpia
        else:
            preserved_lines.append(line)

    # Construir líneas gestionadas (valor del usuario o default)
    managed_lines = [
        f"{key}={existing_values.get(key) or default}"
        for key, default in MANAGED_DEFAULTS.items()
    ]

    # Separador si el archivo no termina en línea vacía
    if preserved_lines and preserved_lines[-1].strip() != "":
        preserved_lines.append("")

    final_text = "\n".join(preserved_lines + managed_lines).rstrip() + "\n"

    # Comparar contra original normalizado — detecta claves faltantes, vacías y duplicados
    if original_text.replace("\r\n", "\n") == final_text:
        return False

    # Escritura atómica: .env → .env.tmp → replace
    tmp = env_path.with_name(f"{env_path.name}.tmp")  # .env → .env.tmp
    tmp.write_text(final_text, encoding="utf-8")
    tmp.replace(env_path)

    return True
