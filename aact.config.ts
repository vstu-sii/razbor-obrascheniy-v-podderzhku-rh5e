// Course reference: https://sii.sergeivolchkov.ru/materials/architecture/aact
// Verified with pinned aact 3.0.0-beta.29; see evidence/aact-check.json.
import type { AactConfig } from "aact";

const config: AactConfig = {
  source: { type: "plantuml", path: "./docs/architecture/container.puml" },
  rules: {
    acyclic: true,
    crud: true,
    acl: true,
    dbPerService: true,
    // Prototype has one responsibility per container; cohesion/stability analysis
    // waits for an executable architecture and team agreement on boundaries.
    cohesion: false,
    stableDependencies: false,
    commonReuse: false,
    // Internal UI reaches backend directly; no public gateway in this MVP.
    apiGateway: false,
  },
};

export default config;
