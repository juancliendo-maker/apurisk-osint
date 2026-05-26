"""LLM Client — wrapper de Anthropic SDK para el Executive Synthesis Engine.

Provee `redactar_narrativa()` y `redactar_insight()` con:
  - Cache en memoria (LRU 256 entradas) por hash del prompt+contexto
  - Timeout duro de 30 segundos
  - Fallback silencioso: retorna None si la key no está, falla la API,
    el modelo está sobrecargado, o cualquier excepción. El llamador
    debe tener su propia plantilla determinística como fallback.
  - Logging mínimo de tokens usados (para auditar costo)

Modelo default: claude-haiku-4-5-20251001
  - ~$1 input / $5 output por MTok
  - Latencia <3s típica para narrativas de 200-300 tokens
  - Suficiente para narrativas ejecutivas cortas (2-6 líneas)

Si necesitas mejor calidad redaccional, cambiar default a
claude-sonnet-4-6 vía env var APURISK_LLM_MODEL.
"""
from __future__ import annotations
import os
import hashlib
import logging
from functools import lru_cache
from typing import Optional

log = logging.getLogger("apurisk.llm")

# ------- Configuración global -------
MODEL_DEFAULT = os.environ.get("APURISK_LLM_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT_S = 30
MAX_TOKENS_DEFAULT = 400

# Counter global para auditar uso (no persistente, solo runtime)
_TOKENS_USADOS = {"input": 0, "output": 0, "llamadas": 0, "fallos": 0,
                   "ultimos_errores": []}  # ring buffer últimos 5 errores


def _hash_key(prompt: str, contexto: str, max_tokens: int, model: str) -> str:
    """Hash estable para cachear narrativas idénticas."""
    h = hashlib.sha1()
    h.update(prompt.encode("utf-8"))
    h.update(b"|")
    h.update(contexto.encode("utf-8"))
    h.update(f"|{max_tokens}|{model}".encode("utf-8"))
    return h.hexdigest()


@lru_cache(maxsize=256)
def _llamar_cached(cache_key: str, prompt: str, contexto: str,
                   max_tokens: int, model: str) -> Optional[str]:
    """Llamada cacheada (LRU 256). cache_key entra para forzar separación."""
    return _llamar_directo(prompt, contexto, max_tokens, model)


def _llamar_directo(prompt: str, contexto: str, max_tokens: int,
                    model: str) -> Optional[str]:
    """Llamada cruda al SDK. Retorna texto o None en cualquier error."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.info("LLM: ANTHROPIC_API_KEY no presente — fallback determinístico")
        return None

    try:
        # Import dentro de la función para no romper deploys sin la dependencia
        from anthropic import Anthropic
    except ImportError:
        log.warning("LLM: paquete 'anthropic' no instalado — fallback")
        return None

    try:
        client = Anthropic(api_key=api_key, timeout=TIMEOUT_S)
        mensaje_user = (
            f"{prompt}\n\n"
            f"--- DATOS DE CONTEXTO ---\n"
            f"{contexto}\n"
            f"--- FIN DATOS ---\n\n"
            f"Redacta tu respuesta directamente, sin preámbulos ni saludos."
        )
        respuesta = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": mensaje_user}],
        )
        # Acumular métricas
        _TOKENS_USADOS["llamadas"] += 1
        try:
            _TOKENS_USADOS["input"] += respuesta.usage.input_tokens
            _TOKENS_USADOS["output"] += respuesta.usage.output_tokens
        except Exception:
            pass
        # Extraer texto
        if respuesta.content and len(respuesta.content) > 0:
            texto = respuesta.content[0].text
            return texto.strip() if texto else None
        return None
    except Exception as e:
        _TOKENS_USADOS["fallos"] += 1
        err_msg = f"{type(e).__name__}: {str(e)[:300]}"
        log.warning("LLM: fallo en llamada → %s", err_msg)
        # Ring buffer: guardar últimos 5 errores únicos
        errores = _TOKENS_USADOS.get("ultimos_errores", [])
        if err_msg not in errores:
            errores.append(err_msg)
            _TOKENS_USADOS["ultimos_errores"] = errores[-5:]
        return None


def redactar_narrativa(prompt: str, contexto: str,
                       max_tokens: int = MAX_TOKENS_DEFAULT,
                       model: Optional[str] = None) -> Optional[str]:
    """API pública: redacta una narrativa corta (3-6 líneas).

    Args:
        prompt: instrucción de qué redactar (rol + tarea).
        contexto: datos estructurados que el LLM debe interpretar.
        max_tokens: límite de tokens de salida (default 400).
        model: override del modelo (default claude-haiku-4-5).

    Returns:
        Texto generado, o None si falla cualquier capa. El llamador
        DEBE tener fallback determinístico.
    """
    model_use = model or MODEL_DEFAULT
    key = _hash_key(prompt, contexto, max_tokens, model_use)
    return _llamar_cached(key, prompt, contexto, max_tokens, model_use)


def redactar_insight(contexto_intel: str,
                     max_tokens: int = 500) -> Optional[str]:
    """Helper: redacta el Executive Insight semanal (4-6 líneas).

    Prompt pre-configurado en rol de analista estratégico Stratfor-style.
    """
    prompt = (
        "Eres un analista senior de inteligencia estratégica especializado en "
        "Perú, minería andina y continuidad operacional. Tu audiencia es un "
        "C-level de una empresa minera o energética con operaciones en el "
        "corredor sur.\n\n"
        "Redacta UN SOLO insight estratégico de 4 a 6 líneas que destile la "
        "señal más importante del momento. Debe:\n"
        " - Identificar UN patrón emergente (no enumerar varios).\n"
        " - Conectar al menos dos categorías de riesgo (ej. político + criminal).\n"
        " - Implicar una consecuencia operacional concreta.\n"
        " - Evitar jerga académica, hashtags, listas o bullets.\n"
        " - Sonar como un párrafo de brief Stratfor: denso, preciso, accionable."
    )
    return redactar_narrativa(prompt, contexto_intel, max_tokens=max_tokens)


def estado_uso() -> dict:
    """Devuelve métricas de uso del LLM en este runtime (para debug)."""
    return dict(_TOKENS_USADOS)


def llm_disponible() -> bool:
    """True si la API key está presente. No verifica que funcione (eso solo
    se sabe al llamar)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
