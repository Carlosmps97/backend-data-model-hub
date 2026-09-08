"""Parser streaming del XML nativo de Erwin → modelo neutro (dataclasses).

Diseñado para los exports "Save As XML" de Erwin 10.x (formato
`<erwin xmlns="http://www.erwin.com/dm">`, ~50MB por modelo). Un solo pase
con `iterparse`; la limpieza de memoria se hace SOLO al cerrar contenedores
(objetos top y tags `*_Groups`) — limpiar cada elemento rompe la lectura de
los Props del padre (lección del análisis del doc 12).

No decide nada de negocio: entrega el grafo tal cual viene (con refs
resueltas). Las decisiones (schemas faltantes, dedup, niveles UDP) viven en
`policies.py`.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Separador del glosario NSM: 0x1F, que el export escapa como literal "\#x1F".
_GLOSS_SEP = re.compile(r"\\#x1F|\x1f")

# Tipos de relación Erwin relevantes.
REL_IDENTIFYING = "2"
REL_NON_IDENTIFYING = "7"
REL_SUBTYPE = "9"          # subcategoría supertipo→subtipo (doc 53)
REL_TABLE_TO_VIEW = "16"

# Null_Option_Type de la RELACIÓN (no confundir con el de atributo 0/1):
# "100" = Nulls Allowed (la FK del hijo admite NULL → el padre es OPCIONAL,
# cardinalidad 0..1) · "101" = No Nulls (padre obligatorio, exactamente 1).
# Evidencia DDV: las 35 identifying (FK⊂PK, no pueden ser NULL) traen todas
# "101"; las non-identifying se reparten 95×"100" + 4×"101".
REL_NULLS_ALLOWED = "100"

# Contenedores que sí se limpian al cerrar (el resto de tags simples se libera
# junto con su contenedor).
_CLEAR_TAGS = {
    "Entity", "View", "Domain", "Relationship", "Key_Group", "Subject_Area",
    "ER_Diagram", "Hive_Database", "Property_Type", "Glossary_Word_List",
    "ER_Model_Shape", "Annotation", "Forward_Engineer_Options_Set",
    "Default_Trigger_Template", "Trigger_Template", "Theme", "ModelProps",
    "Model_Proxy_Object", "Shape", "Page_Style_Sheet", "Drawing_Context_Object",
    "Naming_Standard", "Subtype_Symbol", "History_List_Array",
}


def _t(el: ET.Element) -> str:
    return el.tag.split("}")[-1]


@dataclass
class ErwinAttribute:
    id: str
    owner_id: str
    owner_kind: str            # "Entity" | "View"
    name: str                  # nombre lógico (o físico en vistas)
    physical: str              # User_Formatted_Physical_Name (siempre poblado)
    physical_raw: str          # Physical_Name crudo (puede ser macro %AttName)
    data_type: str             # Physical_Data_Type
    logical_type: str
    nullable: bool             # Null_Option_Type: 0=NULL, 1=NOT NULL
    order: int                 # Physical_Order (1-based)
    definition: str
    comment: str
    domain_ref: str | None
    parent_attr_ref: str | None      # columna origen (FK o passthrough de vista)
    parent_rel_ref: str | None       # relación que la migró
    # Doc 74: Column order de Erwin (Columns_Order_Ref_Array) — el orden que el
    # diagrama y el Table Column Editor muestran por default. Es la base del
    # orden ÚNICO de la plataforma (`policies.column_order`: llaves primero).
    # Sin array cae al Attribute order (Attributes_Order_Ref_Array, que es
    # como Erwin lo inicializa) y, sin éste, al `Physical_Order`.
    column_order: int = 0
    # Doc 69 (facetas): existencia en una sola vista de Erwin
    # (Is_Logical_Only / Is_Physical_Only).
    logical_only: bool = False
    physical_only: bool = False


@dataclass
class ErwinEntity:
    id: str
    name: str                  # nombre lógico
    physical: str              # resuelto (macro → User_Formatted fallback)
    physical_was_macro: bool
    definition: str
    comment: str
    attributes: list[ErwinAttribute] = field(default_factory=list)
    pk_attr_ids: set[str] = field(default_factory=set)
    # Orden REAL de la llave (miembros del Key_Group PK, doc 19 §12b): Erwin
    # muestra el bloque PK del diagrama en ESTE orden, que es independiente
    # del orden físico de columnas (Physical_Order).
    pk_attr_order: list[str] = field(default_factory=list)
    index_key_groups: int = 0  # IF* (inversion entries) — no se migran
    # Doc 69 (facetas): la entidad existe en una sola vista de Erwin.
    logical_only: bool = False
    physical_only: bool = False


@dataclass
class ErwinView:
    id: str
    name: str                  # físico (las vistas Erwin no tienen lógico)
    definition: str
    comment: str
    sql: str = ""              # CREATE VIEW original (ViewProps.SQL — doc 19 §12)
    attributes: list[ErwinAttribute] = field(default_factory=list)


@dataclass
class ErwinRelationship:
    id: str
    name: str
    rel_type: str              # "2" identifying | "7" non-identifying | "9" subtipo | "16" tabla→vista
    cardinality: str           # códigos Erwin: -3, -1, -2, N
    parent_ref: str            # entidad padre (lado PK)
    child_ref: str             # entidad o vista hija (lado FK / derivada)
    null_option: str = ""      # Null_Option_Type: "100" nulls allowed | "101" no nulls


@dataclass
class ErwinSubtypeSymbol:
    """Símbolo de subcategoría (doc 53): el círculo que agrupa las relaciones
    Type 9 de un mismo supertipo. En el XML es un objeto de modelo propio
    (Type 19, `Hide_In_Physical=true`) cuyo `Relationships_Ref_Array` apunta a
    las relaciones del grupo."""
    id: str
    name: str
    rel_refs: list[str] = field(default_factory=list)


@dataclass
class ErwinDomain:
    id: str
    name: str
    builtin: bool
    data_type: str             # Logical_Data_Type
    parent_ref: str | None
    definition: str
    physical_type: str = ""    # Physical_Data_Type (doc 69; data_type = Logical_Data_Type)


@dataclass
class ErwinUdpDef:
    id: str
    full_name: str             # "Entity.Physical.Dominio Principal"
    owner_class: str           # Entity | Attribute | Model | View | ...
    view_mode: str             # Logical | Physical
    short_name: str
    data_type_code: str        # "6" = lista, resto = texto
    default: str
    # Valores permitidos EXPLÍCITOS (tag_Udp_Values_List): la lista COMPLETA
    # de la def, aunque no se use en ninguna tabla. Coma-separada; los valores
    # pueden llevar espacios internos ("CUSTOM_90 days").
    allowed_values: list[str] = field(default_factory=list)


@dataclass
class ErwinDiagram:
    id: str
    name: str
    subject_area: str          # nombre de la SA dueña (por Owner_Path)
    owner_path: str
    shapes: list[tuple[str, str | None]] = field(default_factory=list)
    # (Model_Object_Ref, Anchor_Point crudo o None)


@dataclass
class ErwinModel:
    name: str = ""
    locator: str = ""          # ruta del Mart (erwin://Mart://Mart/<Proy>/<Dom>/<Modelo>)
    entities: dict[str, ErwinEntity] = field(default_factory=dict)
    views: dict[str, ErwinView] = field(default_factory=dict)
    relationships: dict[str, ErwinRelationship] = field(default_factory=dict)
    subtype_symbols: dict[str, ErwinSubtypeSymbol] = field(default_factory=dict)
    domains: dict[str, ErwinDomain] = field(default_factory=dict)
    udp_defs: dict[str, ErwinUdpDef] = field(default_factory=dict)
    # valores UDP explícitos: [(owner_id, owner_tag, def_id, valor)]
    udp_values: list[tuple[str, str, str, str]] = field(default_factory=list)
    glossary: list[tuple[str, ...]] = field(default_factory=list)  # (palabra, abbrev, *alts)
    hive_dbs: dict[str, list[str]] = field(default_factory=dict)   # db name -> member refs
    subject_areas: list[dict] = field(default_factory=list)        # {id,name,definition,order}
    diagrams: list[ErwinDiagram] = field(default_factory=list)
    annotations: int = 0
    subtype_udp_defs: int = 0

    # ---- derivados (post-parse) ----
    def owner_schema(self) -> dict[str, str]:
        """object_id (entidad o vista) → nombre de Hive_Database."""
        out: dict[str, str] = {}
        known = set(self.entities) | set(self.views)
        for db_name, refs in self.hive_dbs.items():
            for r in refs:
                if r in known:
                    out[r] = db_name
        return out

    def view_source_rels(self) -> dict[str, list[ErwinRelationship]]:
        """view_id → relaciones tabla→vista (tipo 16) que la alimentan."""
        out: dict[str, list[ErwinRelationship]] = {}
        for rel in self.relationships.values():
            if rel.rel_type == REL_TABLE_TO_VIEW and rel.child_ref in self.views:
                out.setdefault(rel.child_ref, []).append(rel)
        return out

    def fk_pairs(self) -> dict[str, list[tuple[str, str]]]:
        """rel_id → [(attr_padre_id, attr_hijo_id)] para relaciones tabla-tabla."""
        out: dict[str, list[tuple[str, str]]] = {}
        for ent in self.entities.values():
            for a in ent.attributes:
                if a.parent_attr_ref and a.parent_rel_ref:
                    out.setdefault(a.parent_rel_ref, []).append(
                        (a.parent_attr_ref, a.id))
        return out

    def subtype_symbol_of_rel(self) -> dict[str, str]:
        """rel_id (Type 9) → id del Subtype_Symbol que la agrupa (doc 53)."""
        out: dict[str, str] = {}
        for s in self.subtype_symbols.values():
            for rid in s.rel_refs:
                out[rid] = s.id
        return out

    def attr_index(self) -> dict[str, ErwinAttribute]:
        out: dict[str, ErwinAttribute] = {}
        for coll in (self.entities, self.views):
            for obj in coll.values():
                for a in obj.attributes:
                    out[a.id] = a
        return out


def _props(el: ET.Element, tag: str) -> ET.Element | None:
    return el.find(f"./{{*}}{tag}")


def _phys_sorted(el: ET.Element, props_tag: str, attrs: list[ErwinAttribute]) -> list[ErwinAttribute]:
    """Orden físico CANÓNICO de columnas: el array ordenado del dueño
    (`Physical_Columns_Order_Ref_Array`) — que es lo que Erwin muestra —,
    con fallback al `Physical_Order` numérico por atributo (que puede quedar
    desincronizado tras reordenamientos; doc 19 §12b)."""
    refs = [r.text or "" for r in el.findall(
        f"./{{*}}{props_tag}/{{*}}Physical_Columns_Order_Ref_Array/{{*}}Physical_Columns_Order_Ref")]
    pos = {rid: i for i, rid in enumerate(refs)}
    return sorted(attrs, key=lambda a: (pos.get(a.id, 10**6), a.order))


def _txt(p: ET.Element | None, name: str) -> str:
    return (p.findtext(f"./{{*}}{name}") or "") if p is not None else ""


def parse(xml_path: str) -> ErwinModel:
    """Parsea el XML completo a un ErwinModel (streaming, un pase)."""
    m = ErwinModel()
    stack: list[ET.Element] = []
    # Los Element de la implementación C no aceptan setattr: el estado parcial
    # de un dueño abierto (attrs/keys de hijos ya cerrados) vive en side-dicts
    # por id() del elemento — estable mientras el dueño siga en el stack.
    attrs_of: dict[int, list[ErwinAttribute]] = {}
    keys_of: dict[int, dict] = {}
    kg_member_attr: dict[str, str] = {}  # Key_Group_Member id → Attribute_Ref
    raw_shapes: list[tuple[str, str | None, str]] = []

    for ev, el in ET.iterparse(xml_path, events=("start", "end")):
        tag = _t(el)
        if ev == "start":
            stack.append(el)
            continue
        stack.pop()

        if tag == "UDP_Instance":
            # jerarquía: Owner > OwnerProps > UDP_Instance_Groups > UDP_Instance
            if el.get("Derived") != "Y" and len(stack) >= 3:
                owner = stack[-3]
                m.udp_values.append((owner.get("id") or "", _t(owner),
                                     el.get("id") or "", (el.text or "").strip()))

        elif tag == "Attribute":
            owner = stack[-2] if len(stack) >= 2 else None
            okind = _t(owner) if owner is not None else "?"
            if owner is not None and okind in ("Entity", "View"):
                p = _props(el, "AttributeProps")
                raw = _txt(p, "Physical_Name")
                attr = ErwinAttribute(
                    id=el.get("id") or "",
                    owner_id=owner.get("id") or "",
                    owner_kind=okind,
                    name=el.get("name") or _txt(p, "Name"),
                    physical=_txt(p, "User_Formatted_Physical_Name")
                             or (raw if "%" not in raw else "")
                             or (el.get("name") or ""),
                    physical_raw=raw,
                    data_type=_txt(p, "Physical_Data_Type").strip(),
                    logical_type=_txt(p, "Logical_Data_Type").strip(),
                    nullable=_txt(p, "Null_Option_Type").strip() != "1",
                    order=int(_txt(p, "Physical_Order") or 0),
                    definition=_txt(p, "Definition").strip(),
                    comment=_txt(p, "Comment").strip(),
                    domain_ref=_txt(p, "Parent_Domain_Ref") or None,
                    parent_attr_ref=_txt(p, "Parent_Attribute_Ref") or None,
                    parent_rel_ref=_txt(p, "Parent_Relationship_Ref") or None,
                    logical_only=_txt(p, "Is_Logical_Only").strip() == "true",
                    physical_only=_txt(p, "Is_Physical_Only").strip() == "true",
                )
                # el registro se completa al cerrar el dueño (está en el stack)
                attrs_of.setdefault(id(owner), []).append(attr)

        elif tag == "Key_Group_Member":
            # El miembro del key group es un objeto PROPIO: su Attribute_Ref es
            # el id real del atributo. Los refs del array de miembros del
            # Key_Group apuntan a ESTOS objetos, no a atributos (fix doc 19:
            # sin esta traducción, pk_attr_ids nunca matcheaba y NINGUNA
            # columna migrada quedaba isPrimaryKey).
            p = _props(el, "Key_Group_MemberProps")
            ref = _txt(p, "Attribute_Ref")
            if ref:
                kg_member_attr[el.get("id") or ""] = ref

        elif tag == "Key_Group":
            owner = stack[-2] if len(stack) >= 2 else None
            if owner is not None and _t(owner) == "Entity":
                p = _props(el, "Key_GroupProps")
                ktype = _txt(p, "Key_Group_Type")
                members = [kg_member_attr.get(r.text or "", r.text or "") for r in el.findall(
                    "./{*}Key_GroupProps/{*}Key_Group_Members_Order_Ref_Array/{*}Key_Group_Members_Order_Ref")]
                info = keys_of.setdefault(id(owner), {"pk": set(), "pk_order": [], "if": 0})
                if ktype == "PK":
                    info["pk"].update(members)
                    if not info["pk_order"]:  # una PK por entidad; el array YA viene ordenado
                        info["pk_order"] = list(members)
                elif ktype.startswith("IF"):
                    info["if"] += 1

        elif tag == "Entity":
            p = _props(el, "EntityProps")
            raw = _txt(p, "Physical_Name")
            ufpn = _txt(p, "User_Formatted_Physical_Name")
            was_macro = "%" in raw
            keys = keys_of.pop(id(el), {"pk": set(), "pk_order": [], "if": 0})
            attrs = attrs_of.pop(id(el), [])
            # Doc 74: Column order de Erwin (Columns_Order_Ref_Array); sin array
            # cae al Attribute order y, sin éste, al Physical_Order. El físico
            # de la BD (_phys_sorted) sólo ordena `attributes` para deduplicar.
            lrefs = [r.text or "" for r in el.findall(
                "./{*}EntityProps/{*}Attributes_Order_Ref_Array/{*}Attributes_Order_Ref")]
            lpos = {rid: i for i, rid in enumerate(lrefs)}
            crefs = [r.text or "" for r in el.findall(
                "./{*}EntityProps/{*}Columns_Order_Ref_Array/{*}Columns_Order_Ref")]
            cpos = {rid: i for i, rid in enumerate(crefs)}
            for a in attrs:
                a.column_order = cpos.get(a.id, lpos.get(a.id, a.order))
            ent = ErwinEntity(
                id=el.get("id") or "",
                name=el.get("name") or _txt(p, "Name"),
                physical=(ufpn if was_macro or not raw else raw) or raw,
                physical_was_macro=was_macro,
                definition=_txt(p, "Definition").strip(),
                comment=_txt(p, "Comment").strip(),
                attributes=_phys_sorted(el, "EntityProps", attrs),
                pk_attr_ids=set(keys["pk"]),
                pk_attr_order=list(keys.get("pk_order") or []),
                index_key_groups=keys["if"],
                logical_only=_txt(p, "Is_Logical_Only").strip() == "true",
                physical_only=_txt(p, "Is_Physical_Only").strip() == "true",
            )
            m.entities[ent.id] = ent

        elif tag == "View":
            p = _props(el, "ViewProps")
            v = ErwinView(
                id=el.get("id") or "",
                name=el.get("name") or _txt(p, "Name"),
                definition=_txt(p, "Definition").strip(),
                comment=_txt(p, "Comment").strip(),
                sql=_txt(p, "SQL").strip(),
                attributes=_phys_sorted(el, "ViewProps", attrs_of.pop(id(el), [])),
            )
            m.views[v.id] = v

        elif tag == "Relationship":
            p = _props(el, "RelationshipProps")
            r = ErwinRelationship(
                id=el.get("id") or "",
                name=el.get("name") or "",
                rel_type=_txt(p, "Type").strip(),
                cardinality=_txt(p, "Cardinality").strip(),
                parent_ref=_txt(p, "Parent_Entity_Ref"),
                child_ref=_txt(p, "Child_Entity_Ref"),
                null_option=_txt(p, "Null_Option_Type").strip(),
            )
            m.relationships[r.id] = r

        elif tag == "Domain":
            p = _props(el, "DomainProps")
            d = ErwinDomain(
                id=el.get("id") or "",
                name=el.get("name") or _txt(p, "Name"),
                builtin=bool(_txt(p, "Built_In_Id")),
                data_type=_txt(p, "Logical_Data_Type").strip(),
                parent_ref=_txt(p, "Parent_Domain_Ref") or None,
                definition=_txt(p, "Definition").strip(),
                physical_type=_txt(p, "Physical_Data_Type").strip(),
            )
            m.domains[d.id] = d

        elif tag == "Property_Type":
            full = el.get("name") or ""
            parts = full.split(".")
            if parts and parts[0] == "Subtype_Symbol":
                m.subtype_udp_defs += 1
            elif len(parts) >= 3:
                p = _props(el, "Property_TypeProps")
                raw_vals = _txt(p, "tag_Udp_Values_List")
                m.udp_defs[el.get("id") or ""] = ErwinUdpDef(
                    id=el.get("id") or "",
                    full_name=full,
                    owner_class=parts[0],
                    view_mode=parts[1],
                    short_name=".".join(parts[2:]),
                    data_type_code=_txt(p, "tag_Udp_Data_Type").strip()
                                   or _txt(p, "Data_Type").strip(),
                    default=(_txt(p, "tag_Udp_Default_Value") or "").strip(),
                    # lista explícita coma-separada (valores con espacios internos)
                    allowed_values=[v.strip() for v in raw_vals.split(",") if v.strip()]
                                   if raw_vals else [],
                )

        elif tag == "Glossary_Word_List":
            fields = [x.strip() for x in _GLOSS_SEP.split(el.text or "")]
            if fields and fields[0]:
                m.glossary.append(tuple(fields))

        elif tag == "Hive_Database":
            refs = [r.text or "" for r in el.findall(".//{*}Dependent_Objects_Ref")]
            name = el.get("name") or ""
            if name:
                m.hive_dbs.setdefault(name, []).extend(refs)

        elif tag == "Subtype_Symbol":
            # doc 53: el contenido útil es el array de relaciones del grupo;
            # el resto (UDP de estilo, shapes) sigue siendo ruido y el tag
            # permanece en _CLEAR_TAGS (la limpieza corre después del handler).
            p = _props(el, "Subtype_SymbolProps")
            refs = [r.text or "" for r in el.findall(
                "./{*}Subtype_SymbolProps/{*}Relationships_Ref_Array/{*}Relationships_Ref")]
            s = ErwinSubtypeSymbol(
                id=el.get("id") or "",
                name=el.get("name") or _txt(p, "Name"),
                rel_refs=[r for r in refs if r],
            )
            m.subtype_symbols[s.id] = s

        elif tag == "Subject_Area":
            p = _props(el, "Subject_AreaProps")
            m.subject_areas.append({
                "id": el.get("id") or "",
                "name": el.get("name") or _txt(p, "Name"),
                "definition": _txt(p, "Definition").strip(),
                "order": int(_txt(p, "Object_Order") or 0),
            })

        elif tag == "ER_Model_Shape":
            p = _props(el, "ER_Model_ShapeProps")
            if p is not None:
                # se adjunta por Owner_Path al resolver diagramas (patrón probado)
                raw_shapes.append((_txt(p, "Model_Object_Ref"),
                                   _txt(p, "Anchor_Point").strip() or None,
                                   _txt(p, "Owner_Path")))

        elif tag == "ER_Diagram":
            p = _props(el, "ER_DiagramProps")
            op = _txt(p, "Owner_Path")
            m.diagrams.append(ErwinDiagram(
                id=el.get("id") or "",
                name=el.get("name") or _txt(p, "Name"),
                subject_area=op.split(".")[-1] if "." in op else op,
                owner_path=op,
            ))

        elif tag == "Locator":
            # Ruta del Mart del modelo — capa de origen de carpetas (doc 54 §9).
            if not m.locator:
                m.locator = (el.text or "").strip()

        elif tag == "Annotation":
            m.annotations += 1

        elif tag == "Model":
            p = _props(el, "ModelProps")
            m.name = m.name or el.get("name") or _txt(p, "Name")

        elif tag == "ModelProps" and not m.name:
            m.name = _txt(el, "Name")

        if tag in _CLEAR_TAGS or tag.endswith("_Groups"):
            el.clear()

    # asociar shapes → diagramas por Owner_Path ("Modelo.SA.Diagrama")
    by_path = {f"{d.owner_path}.{d.name}": d for d in m.diagrams}
    for ref, anchor, op in raw_shapes:
        d = by_path.get(op)
        if d is not None:
            d.shapes.append((ref, anchor))

    # SA dueña del diagrama por Owner_Path ("Modelo.SA"): el nombre de la SA
    # puede llevar puntos ("1. Party") y el split ingenuo del handler trunca
    # (" Party") — se re-resuelve contra los nombres REALES de SA, ganando el
    # que sea sufijo más largo del path.
    sa_names = {sa["name"] for sa in m.subject_areas if sa["name"]}
    for d in m.diagrams:
        if d.subject_area in sa_names:
            continue
        for n in sorted(sa_names, key=len, reverse=True):
            if d.owner_path == n or d.owner_path.endswith("." + n):
                d.subject_area = n
                break
    return m
