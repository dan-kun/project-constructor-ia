"""Tests del Agente Verificador con FakeProvider (sin llamadas reales ni Docker)."""

import json
import subprocess

import pytest

from conftest import FakeProvider
from pcia.agents import verificador as modulo_verificador
from pcia.agents.llm_json import ContratoInvalidoError
from pcia.agents.verificador import Verificador
from pcia.domain.models import Chequeo, VerificacionProfunda


def crear_verificador(respuestas=None):
    return Verificador(FakeProvider(respuestas or []))


def escribir(raiz, relativa, contenido):
    ruta = raiz / relativa
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8")


def test_proyecto_valido_aprueba(tmp_path):
    escribir(tmp_path, "src/main.py", "def hola():\n    return 1\n")
    escribir(tmp_path, "config.json", '{"ok": true}')
    escribir(tmp_path, "ci.yml", "jobs:\n  test:\n    runs-on: ubuntu\n")
    escribir(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    escribir(tmp_path, "vista.xml", "<odoo><record id='x'/></odoo>")
    escribir(tmp_path, "accesos.csv", "id,name\n1,a\n2,b\n")

    resultado = crear_verificador().verificar(tmp_path)

    assert resultado.aprobado()
    assert {c.estado for c in resultado.chequeos} == {"ok"}


def test_archivos_sin_verificador_se_omiten_y_no_bloquean(tmp_path):
    escribir(tmp_path, "docs/notas.md", "# hola")
    escribir(tmp_path, "src/main.ts", "const x: número =")  # ni se intenta

    resultado = crear_verificador().verificar(tmp_path)

    assert resultado.aprobado()
    assert {c.estado for c in resultado.chequeos} == {"omitido"}
    # omitidos por diseño: no obligatorios, no dejan el estado inconcluso
    assert not any(c.obligatorio for c in resultado.chequeos)


# --- consistencia del README (referencias a archivos) ----------------------------


def test_readme_con_referencias_existentes_aprueba(tmp_path):
    escribir(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    escribir(tmp_path, ".env.example", "APP_ENV=dev\n")
    escribir(
        tmp_path,
        "README.md",
        "# demo\n\nConfigurar `.env.example`.\n\n```bash\n"
        "pip install -e .\npytest\ncat pyproject.toml\n```\n",
    )

    chequeo = crear_verificador().verificar_archivo(tmp_path, "README.md")

    assert chequeo.estado == "ok"
    assert "2 referencia(s)" in chequeo.detalle


def test_readme_que_referencia_archivos_inexistentes_falla(tmp_path):
    escribir(
        tmp_path,
        "README.md",
        "# demo\n\n```bash\npip install -r requirements.txt\n"
        "docker-compose up\n```\n\nVer `docker-compose.yml`.\n",
    )

    chequeo = crear_verificador().verificar_archivo(tmp_path, "README.md")

    assert chequeo.estado == "error"
    assert "docker-compose.yml" in chequeo.detalle
    assert "requirements.txt" in chequeo.detalle


def test_referencias_ignoran_urls_modulos_y_comandos(tmp_path):
    escribir(
        tmp_path,
        "README.md",
        "# demo\n\n```bash\n"
        "uvicorn mi_api.main:app --reload   # http://localhost:8000\n"
        "npm run start:dev\nnpm install\n```\n",
    )

    chequeo = crear_verificador().verificar_archivo(tmp_path, "README.md")

    assert chequeo.estado == "ok"
    assert "0 referencia(s)" in chequeo.detalle


def test_referencias_solo_cuentan_dentro_de_bloques_de_codigo(tmp_path):
    # la prosa puede nombrar tecnologías tipo Node.js sin que sean archivos
    escribir(tmp_path, "README.md", "# demo\n\nHecho con Node.js y FastAPI.\n")

    chequeo = crear_verificador().verificar_archivo(tmp_path, "README.md")

    assert chequeo.estado == "ok"


@pytest.mark.parametrize(
    "relativa, contenido",
    [
        ("roto.py", "def hola(:\n"),
        ("roto.json", "{roto"),
        ("roto.yaml", "clave: [sin cerrar"),
        ("roto.toml", "name = sin comillas ni tipo\n= x"),
        ("roto.xml", "<odoo><sin_cerrar></odoo>"),
        ("roto.csv", "id,name\n1\n2,b,c\n"),
    ],
)
def test_sintaxis_rota_se_detecta(tmp_path, relativa, contenido):
    escribir(tmp_path, relativa, contenido)

    resultado = crear_verificador().verificar(tmp_path)

    assert not resultado.aprobado()
    assert resultado.errores()[0].archivo == relativa
    assert resultado.errores()[0].detalle  # el error trae el detalle para corregir


def test_verificar_archivo_individual(tmp_path):
    escribir(tmp_path, "a.json", "{roto")
    chequeo = crear_verificador().verificar_archivo(tmp_path, "a.json")
    assert chequeo.estado == "error"

    escribir(tmp_path, "a.json", '{"ok": 1}')
    chequeo = crear_verificador().verificar_archivo(tmp_path, "a.json")
    assert chequeo.estado == "ok"


def test_corregir_archivo_reescribe_con_lo_que_devuelve_el_llm(tmp_path):
    escribir(tmp_path, "config.json", "{roto")
    correccion = json.dumps({"contenido_corregido": '{"ok": true}\n'})
    verificador = Verificador(FakeProvider([correccion]))

    verificador.corregir_archivo(tmp_path, "config.json", "json inválido")

    assert (tmp_path / "config.json").read_text(encoding="utf-8") == '{"ok": true}\n'


def test_prompt_de_correccion_incluye_ruta_error_y_contenido(tmp_path):
    escribir(tmp_path, "config.json", "{roto")
    provider = FakeProvider([json.dumps({"contenido_corregido": "{}"})])

    Verificador(provider).corregir_archivo(tmp_path, "config.json", "Expecting value")

    system_prompt, _ = provider.llamadas[0]
    assert "config.json" in system_prompt
    assert "Expecting value" in system_prompt
    assert "{roto" in system_prompt
    assert "[[" not in system_prompt


def test_correccion_malformada_reintenta_y_luego_escala(tmp_path):
    escribir(tmp_path, "config.json", "{roto")
    verificador = Verificador(FakeProvider(["basura"] * 3))

    with pytest.raises(ContratoInvalidoError):
        verificador.corregir_archivo(tmp_path, "config.json", "json inválido")
    # el archivo no se tocó
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == "{roto"


# --- corrector de builds (Fase 7) ---------------------------------------------


ERROR_BUILD = [
    Chequeo(archivo="docker-build", estado="error", detalle="código 1: npm ci falló")
]


def scaffold_nestjs_minimo(tmp_path):
    escribir(tmp_path, "Dockerfile", "RUN npm ci\n")
    escribir(tmp_path, "package.json", '{"name": "demo"}')
    escribir(tmp_path, "README.md", "# demo")
    escribir(tmp_path, "docs/adr/ADR-001-decisiones-iniciales.md", "# ADR")
    return ["Dockerfile", "package.json", "README.md", "docs/adr/ADR-001-decisiones-iniciales.md"]


def correccion_build_json(archivos_corregidos, diagnostico="faltaba el lockfile"):
    return json.dumps(
        {
            "diagnostico": diagnostico,
            "correcciones": [
                {"archivo": a, "contenido_corregido": c} for a, c in archivos_corregidos
            ],
        }
    )


def test_corregir_build_aplica_correcciones_multiarchivo(tmp_path):
    archivos = scaffold_nestjs_minimo(tmp_path)
    respuesta = correccion_build_json(
        [("Dockerfile", "RUN npm install\n"), ("package.json", '{"name": "demo2"}')]
    )
    verificador = Verificador(FakeProvider([respuesta]))

    correccion = verificador.corregir_build(tmp_path, ERROR_BUILD, archivos)

    assert correccion.diagnostico == "faltaba el lockfile"
    assert (tmp_path / "Dockerfile").read_text(encoding="utf-8") == "RUN npm install\n"
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == '{"name": "demo2"}'


def test_corregir_build_rechaza_archivos_fuera_del_scaffold_y_reintenta(tmp_path):
    archivos = scaffold_nestjs_minimo(tmp_path)
    invalida = correccion_build_json([("../afuera.txt", "x")])
    valida = correccion_build_json([("Dockerfile", "RUN npm install\n")])
    provider = FakeProvider([invalida, valida])

    Verificador(provider).corregir_build(tmp_path, ERROR_BUILD, archivos)

    assert len(provider.llamadas) == 2
    _, mensajes_reintento = provider.llamadas[1]
    assert "afuera.txt" in mensajes_reintento[-1].content
    assert not (tmp_path.parent / "afuera.txt").exists()


def test_corregir_build_sin_cambios_devuelve_diagnostico_y_no_escribe(tmp_path):
    archivos = scaffold_nestjs_minimo(tmp_path)
    respuesta = correccion_build_json([], diagnostico="falla de red del registry")
    verificador = Verificador(FakeProvider([respuesta]))

    correccion = verificador.corregir_build(tmp_path, ERROR_BUILD, archivos)

    assert correccion.correcciones == []
    assert "falla de red" in correccion.diagnostico
    assert (tmp_path / "Dockerfile").read_text(encoding="utf-8") == "RUN npm ci\n"


def test_prompt_del_corrector_incluye_errores_y_scaffold_sin_docs(tmp_path):
    archivos = scaffold_nestjs_minimo(tmp_path)
    provider = FakeProvider([correccion_build_json([("Dockerfile", "ok\n")])])

    Verificador(provider).corregir_build(tmp_path, ERROR_BUILD, archivos)

    system_prompt, _ = provider.llamadas[0]
    assert "npm ci falló" in system_prompt  # el error del build
    assert "RUN npm ci" in system_prompt  # contenido del Dockerfile
    assert '{"name": "demo"}' in system_prompt  # contenido del package.json
    assert "ADR-001" not in system_prompt  # README/docs no gastan contexto
    assert "[[" not in system_prompt


def test_corrector_persistentemente_malformado_escala_sin_escribir(tmp_path):
    archivos = scaffold_nestjs_minimo(tmp_path)
    verificador = Verificador(FakeProvider(["basura"] * 3))

    with pytest.raises(ContratoInvalidoError):
        verificador.corregir_build(tmp_path, ERROR_BUILD, archivos)
    assert (tmp_path / "Dockerfile").read_text(encoding="utf-8") == "RUN npm ci\n"


# --- verificación profunda (Fase 4b) -----------------------------------------


VERIFICACIONES = [
    VerificacionProfunda(id="docker-build", tipo="docker_build"),
    VerificacionProfunda(
        id="smoke", tipo="docker_run", comando=["python", "-c", "import x"]
    ),
    VerificacionProfunda(
        id="lint", tipo="comando", requiere="ruff", comando=["ruff", "check", "."]
    ),
]


class EjecutorFalso:
    """Registra los comandos ejecutados y devuelve resultados programados."""

    def __init__(self, codigos=None):
        self.codigos = dict(codigos or {})  # comando[0..1] -> returncode
        self.comandos = []

    def __call__(self, comando, cwd, timeout):
        self.comandos.append((list(comando), cwd, timeout))
        codigo = self.codigos.get(" ".join(comando[:2]), 0)
        return subprocess.CompletedProcess(comando, codigo, stdout="", stderr="falló")


def profundos(monkeypatch, tmp_path, binarios, codigos=None, verificaciones=None):
    ejecutor = EjecutorFalso(codigos)
    monkeypatch.setattr(modulo_verificador, "_ejecutar", ejecutor)
    monkeypatch.setattr(
        modulo_verificador, "_binario_disponible", lambda nombre: nombre in binarios
    )
    chequeos = crear_verificador().verificar_profundo(
        tmp_path, verificaciones or VERIFICACIONES, "pcia-verif-demo"
    )
    return chequeos, ejecutor


def test_sin_docker_los_chequeos_docker_se_omiten(monkeypatch, tmp_path):
    chequeos, ejecutor = profundos(monkeypatch, tmp_path, binarios=set())

    estados = {c.archivo: c.estado for c in chequeos}
    assert estados == {"docker-build": "omitido", "smoke": "omitido", "lint": "omitido"}
    assert "docker no está disponible" in chequeos[0].detalle
    assert ejecutor.comandos == []  # no se ejecutó nada


def test_build_y_smoke_ok_con_limpieza_de_imagen(monkeypatch, tmp_path):
    chequeos, ejecutor = profundos(monkeypatch, tmp_path, binarios={"docker", "ruff"})

    assert [c.estado for c in chequeos] == ["ok", "ok", "ok"]
    comandos = [c[0] for c in ejecutor.comandos]
    # límites de recursos (docs/SEGURIDAD.md R2): el build necesita red para
    # instalar dependencias, el smoke test no.
    assert comandos[0] == [
        "docker", "build", "--memory", "1g", "--cpu-quota", "200000",
        "-t", "pcia-verif-demo", ".",
    ]
    assert "--network" not in comandos[0]
    assert comandos[1] == [
        "docker", "run", "--rm", "--memory", "1g", "--cpus", "2", "--network", "none",
        "pcia-verif-demo", "python", "-c", "import x",
    ]
    assert comandos[2] == ["ruff", "check", "."]
    assert comandos[3][:2] == ["docker", "rmi"]  # limpieza best-effort
    assert all(c[1] == tmp_path for c in ejecutor.comandos)


def test_build_fallido_reporta_error_y_omite_el_smoke(monkeypatch, tmp_path):
    chequeos, ejecutor = profundos(
        monkeypatch, tmp_path, binarios={"docker"}, codigos={"docker build": 1}
    )

    por_id = {c.archivo: c for c in chequeos}
    assert por_id["docker-build"].estado == "error"
    assert "falló" in por_id["docker-build"].detalle
    assert por_id["smoke"].estado == "omitido"
    assert "requiere una imagen construida" in por_id["smoke"].detalle
    # sin build exitoso no hay limpieza de imagen
    assert not any(c[0][:2] == ["docker", "rmi"] for c in ejecutor.comandos)


def test_linter_no_disponible_se_omite_y_no_bloquea(monkeypatch, tmp_path):
    chequeos, _ = profundos(monkeypatch, tmp_path, binarios={"docker"})

    lint = next(c for c in chequeos if c.archivo == "lint")
    assert lint.estado == "omitido"
    assert "'ruff' no está disponible" in lint.detalle


def test_timeout_en_un_chequeo_es_error(monkeypatch, tmp_path):
    def ejecutar_con_timeout(comando, cwd, timeout):
        raise subprocess.TimeoutExpired(comando, timeout)

    monkeypatch.setattr(modulo_verificador, "_ejecutar", ejecutar_con_timeout)
    monkeypatch.setattr(modulo_verificador, "_binario_disponible", lambda _: True)

    chequeos = crear_verificador().verificar_profundo(
        tmp_path,
        [VerificacionProfunda(id="lento", tipo="comando", comando=["algo"])],
        "pcia-verif-demo",
    )
    assert chequeos[0].estado == "error"
    assert "timeout" in chequeos[0].detalle
