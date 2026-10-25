"""Contrato de entrada: conserva los campos existentes y rechaza tipos inválidos."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Texto = Annotated[str, Field(max_length=4096)]


class EquipoEntrada(BaseModel):
    # La UI envía texto; los contadores numéricos siguen siendo compatibles.
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)
    sn: Annotated[str, Field(max_length=256)]
    marca: Texto | None = None
    modelo: Texto | None = None
    tipo: Texto | None = None
    sede: Texto | None = None
    zona: Texto | None = None
    domicilio: Texto | None = None
    ubicacion: Texto | None = None
    piso: Texto | None = None
    ip_fisica: Texto | None = None
    ip_hp: Texto | None = None
    ip_ekm: Texto | None = None
    perfil: Texto | None = None
    estatus: Texto | None = None
    contador_inicial: Texto | None = None
    sustituto_sn: Texto | None = None
    origen_hoja: Texto | None = None
    confirmar_ubicacion: bool = False


class BorradoEquiposEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")
    confirm: Annotated[str, Field(max_length=16)] = ""
    q: Texto | None = None
    sede: Texto | None = None
    marca: Texto | None = None
    estatus: Texto | None = None
    zona: Texto | None = None
