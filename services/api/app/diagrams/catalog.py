"""Validated curated diagram and concept catalog."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

StableId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")]


class ConceptDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_id: StableId
    display_name: str = Field(min_length=1, max_length=100)
    aliases: tuple[str, ...] = Field(min_length=1, max_length=20)
    retrieval_query: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_names(self):
        names = (self.display_name, *self.aliases)
        if any(not name.strip() for name in names):
            raise ValueError("Concept names and aliases must not be blank")
        folded = [name.strip().casefold() for name in names]
        if len(folded) != len(set(folded)):
            raise ValueError("Concept names and aliases must be unique")
        return self


class RegionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    region_id: StableId
    concept_id: StableId
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Region geometry must fit within normalized diagram bounds")
        return self


class DiagramDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    diagram_id: StableId
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    image_url: str = Field(pattern=r"^/diagrams/[a-z0-9._-]+\.svg$")
    image_aspect_ratio: float = Field(gt=0, allow_inf_nan=False)
    concepts: tuple[ConceptDefinition, ...] = Field(min_length=1)
    regions: tuple[RegionDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self):
        concept_ids = [concept.concept_id for concept in self.concepts]
        if len(concept_ids) != len(set(concept_ids)):
            raise ValueError("Concept IDs must be unique within a diagram")
        region_ids = [region.region_id for region in self.regions]
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("Region IDs must be unique within a diagram")
        concept_set = set(concept_ids)
        if any(region.concept_id not in concept_set for region in self.regions):
            raise ValueError("Every region must refer to a configured concept")

        aliases = {concept_id.casefold() for concept_id in concept_ids}
        for concept in self.concepts:
            for name in (concept.display_name, *concept.aliases):
                key = name.strip().casefold()
                if key in aliases:
                    raise ValueError("Concept names and aliases must not be ambiguous")
                aliases.add(key)

        for index, left in enumerate(self.regions):
            for right in self.regions[index + 1 :]:
                overlaps = (
                    left.x < right.x + right.width
                    and right.x < left.x + left.width
                    and left.y < right.y + right.height
                    and right.y < left.y + left.height
                )
                if overlaps:
                    raise ValueError("Selectable region geometry must not overlap")
        return self


PHOTOSYNTHESIS = DiagramDefinition(
    diagram_id="photosynthesis-cycle",
    title="Photosynthesis",
    description=(
        "An authored overview of light, plant inputs, chlorophyll, and photosynthesis outputs. "
        "Select a labeled region to search your indexed study materials."
    ),
    image_url="/diagrams/photosynthesis.svg",
    image_aspect_ratio=1000 / 640,
    concepts=(
        ConceptDefinition(
            concept_id="biology.photosynthesis.sunlight",
            display_name="Light energy",
            aliases=("sunlight", "light"),
            retrieval_query=(
                "How plants absorb light energy from sunlight for photosynthesis and chlorophyll"
            ),
        ),
        ConceptDefinition(
            concept_id="biology.photosynthesis.inputs",
            display_name="Water and carbon dioxide",
            aliases=("water", "carbon dioxide", "reactants"),
            retrieval_query=(
                "What water and carbon dioxide contribute as inputs to plant photosynthesis"
            ),
        ),
        ConceptDefinition(
            concept_id="biology.photosynthesis.chlorophyll",
            display_name="Chlorophyll in leaves",
            aliases=("chlorophyll", "leaf", "leaves"),
            retrieval_query=(
                "How chlorophyll in plant leaves absorbs light energy during photosynthesis"
            ),
        ),
        ConceptDefinition(
            concept_id="biology.photosynthesis.glucose",
            display_name="Glucose",
            aliases=("sugar", "stored chemical energy"),
            retrieval_query=(
                "How photosynthesis produces glucose and stores energy as chemical energy"
            ),
        ),
        ConceptDefinition(
            concept_id="biology.photosynthesis.oxygen",
            display_name="Oxygen",
            aliases=("oxygen gas", "O2"),
            retrieval_query=("How oxygen is produced and released during photosynthesis"),
        ),
    ),
    regions=(
        RegionDefinition(
            region_id="sunlight",
            concept_id="biology.photosynthesis.sunlight",
            x=0.025,
            y=0.08,
            width=0.25,
            height=0.28,
        ),
        RegionDefinition(
            region_id="inputs",
            concept_id="biology.photosynthesis.inputs",
            x=0.025,
            y=0.62,
            width=0.31,
            height=0.27,
        ),
        RegionDefinition(
            region_id="chlorophyll",
            concept_id="biology.photosynthesis.chlorophyll",
            x=0.36,
            y=0.25,
            width=0.28,
            height=0.5,
        ),
        RegionDefinition(
            region_id="glucose",
            concept_id="biology.photosynthesis.glucose",
            x=0.70,
            y=0.12,
            width=0.275,
            height=0.28,
        ),
        RegionDefinition(
            region_id="oxygen",
            concept_id="biology.photosynthesis.oxygen",
            x=0.70,
            y=0.60,
            width=0.275,
            height=0.28,
        ),
    ),
)

DIAGRAMS = {PHOTOSYNTHESIS.diagram_id: PHOTOSYNTHESIS}


def get_diagram(diagram_id: str) -> DiagramDefinition | None:
    return DIAGRAMS.get(diagram_id)


def resolve_region(
    diagram_id: str, region_id: str
) -> tuple[DiagramDefinition, RegionDefinition, ConceptDefinition] | None:
    diagram = get_diagram(diagram_id)
    if diagram is None:
        return None
    region = next((item for item in diagram.regions if item.region_id == region_id), None)
    if region is None:
        return None
    concept = next(item for item in diagram.concepts if item.concept_id == region.concept_id)
    return diagram, region, concept


def resolve_concept_alias(diagram_id: str, value: str) -> ConceptDefinition | None:
    diagram = get_diagram(diagram_id)
    if diagram is None:
        return None
    normalized = value.strip().casefold()
    return next(
        (
            concept
            for concept in diagram.concepts
            if normalized
            in {concept.concept_id.casefold(), concept.display_name.casefold()}
            | {alias.casefold() for alias in concept.aliases}
        ),
        None,
    )
