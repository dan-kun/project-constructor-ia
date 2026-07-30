"""Adapter web (Fase 8): interfaz HTTP sobre el mismo Orquestador que usa el CLI.

No es un cambio de dominio: es una segunda forma de "entrada"/"salida" para
``Orquestador`` (igual que ``cli.py``), inyectada como callables. El
Orquestador corre bloqueante en un hilo de fondo por sesión; ``entrada`` y
``salida`` se puentean a HTTP vía colas (ver ``sessions.py``).
"""
