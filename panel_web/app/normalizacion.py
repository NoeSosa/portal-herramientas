"""Normalización de seriales y ubicaciones del inventario.

No toca la base ni la UI: solo convierte texto a una clave estable para evitar
duplicados por mayúsculas, espacios, acentos o variantes como "2 penal" /
"Segundos Penal" / "Juzgado Segundo en Materia Penal".
"""

from __future__ import annotations

import re
import unicodedata


_NUMEROS = {
    "primer": "1", "primero": "1", "primera": "1",
    "segundo": "2", "segunda": "2", "segundos": "2",
    "tercer": "3", "tercero": "3", "tercera": "3", "terceros": "3",
    "cuarto": "4", "cuarta": "4", "cuartos": "4",
    "quinto": "5", "quinta": "5", "quintos": "5",
    "sexto": "6", "sexta": "6", "sextos": "6",
    "septimo": "7", "septima": "7", "septimos": "7",
    "octavo": "8", "octava": "8", "octavos": "8",
    "noveno": "9", "novena": "9", "novenos": "9",
    "decimo": "10", "decima": "10", "decimos": "10",
}

_STOPWORDS = {
    "juzgado", "juzgada", "juzgados", "de", "del", "en", "materia",
    "el", "la", "los", "las", "para", "y", "e", "al", "a",
}


def _sin_acentos(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def normalizar_serial(serial: str) -> str:
    """Clave de serial: mayúsculas, sin espacios/guiones y O/I/L corregidas.

    La corrección O→0 e I/L→1 solo aplica después del primer dígito, igual que
    en el motor OCR. Así `Mx3cv6Q0DZ` y `MX3CV6QODZ` producen la misma clave.
    """
    texto = re.sub(r"[^A-Z0-9]", "", _sin_acentos(str(serial or "")).upper())
    salida: list[str] = []
    hay_digito = False
    for c in texto:
        if c.isdigit():
            hay_digito = True
        if hay_digito and c == "O":
            salida.append("0")
        elif hay_digito and c in {"I", "L"}:
            salida.append("1")
        else:
            salida.append(c)
    return "".join(salida)


def normalizar_serial_alterno(serial: str) -> str:
    """Segunda verificación para confusiones OCR frecuentes en cualquier posición.

    No sustituye la clave primaria: sólo se usa cuando ésta no encontró una
    coincidencia única. Cubre O/0, I/L/1 y S/5, por ejemplo:

      VNBOB04743 -> VNB0B04743
      CNBINDROXQ -> CNB1NDR0XQ
      HPS40A92   -> HP540A92

    Si la variante coincide con más de una serie del inventario, se marca como
    ambigua y no se elige automáticamente.
    """
    return normalizar_serial(serial).translate(str.maketrans({
        "O": "0",
        "I": "1",
        "L": "1",
        "S": "5",
    }))


def normalizar_ubicacion(ubicacion: str) -> str:
    """Clave canónica de una ubicación.

    Ejemplos:
      "Segundos Penal"                 -> "2 penal"
      "2 penal"                        -> "2 penal"
      "Juzgado Segundo en Materia Penal" -> "2 penal"
      "Juzgado Segundo en Materia Penal" -> "2 penal"
    """
    texto = _sin_acentos(str(ubicacion or "")).lower()
    texto = texto.replace("2do", "2").replace("2da", "2")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    # "2o", "3ro", "4to", "1ro": el ordinal no cambia la ubicación.
    texto = re.sub(r"\b(\d+)(?:o|a|do|da|ro|ra|to|ta|mo|ma|vo|va|no|na)\b", r"\1", texto)
    tokens: list[str] = []
    for token in texto.split():
        token = _NUMEROS.get(token, token)
        if token in _STOPWORDS:
            continue
        if token.endswith("es") and token[:-2] in _NUMEROS:
            token = _NUMEROS[token[:-2]]
        if token.endswith("s") and token[:-1] in _NUMEROS:
            token = _NUMEROS[token[:-1]]
        if token == "penales":
            token = "penal"
        if token:
            tokens.append(token)
    return " ".join(sorted(tokens))


def nombre_ubicacion_legible(ubicacion: str) -> str:
    """Limpia espacios y mayúsculas raras sin cambiar el nombre elegido."""
    return re.sub(r"\s+", " ", str(ubicacion or "")).strip()
