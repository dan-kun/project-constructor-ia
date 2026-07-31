"""Adaptador web: interfaz de navegador sobre el mismo orquestador.

El orquestador recibe su IO como callables (``entrada`` / ``salida``), así
que la consola siempre fue un adaptador: esta interfaz se enchufa en el
mismo puerto sin tocar el dominio ni los agentes.
"""
