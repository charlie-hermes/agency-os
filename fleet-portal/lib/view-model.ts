export const portalSnapshot = {
  launch: {
    completion: 100,
    sources: 3,
    approvedFacts: 8,
    openQuestions: 0,
  },
  modules: [
    { name: "Content Engine", state: "Active", detail: "Controlled content production and QA" },
    { name: "Living Brand Twin", state: "Active", detail: "Approved facts, claims and evidence" },
    { name: "AI Market Observatory", state: "Active", detail: "Repeatable AI-market missions" },
    { name: "Brand Agent", state: "Active", detail: "Evidence-bound private experience" },
  ],
  content: [
    {
      id: "content_fleet_controlled_1",
      title: "Fleet AI readiness introduction",
      type: "Article",
      state: "Controlled preview",
      note: "One materialised G2.6 catalogue item. No historical work has been invented.",
    },
  ],
  aiPresence: {
    missions: 5,
    finding: "Fleet's category and value are represented consistently in the approved internal evidence set.",
    limitation: "This is controlled internal evidence, not a permanent public ranking.",
  },
} as const;
