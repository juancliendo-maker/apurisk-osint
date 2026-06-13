"""APURISK · paquete web — app FastAPI dividida en módulos temáticos.

server.py (en el paquete raíz) sigue siendo el punto de entrada
(`uvicorn apurisk.server:app`); este paquete contiene las piezas:
  - core: configuración y utilidades compartidas
  - security: middleware de acceso + login
  - schedulers: tareas de fondo y arranque
  - routes_*: endpoints agrupados por tema
"""
