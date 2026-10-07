// ---------------------------------------------------------------------------
// Central mock data source. Swap any of this for real API calls later —
// components only depend on the shapes below, not on where the data comes from.
// ---------------------------------------------------------------------------

// Each division owns its own set of reports + slash-variables, so switching
// division in the sidebar changes what shows up under "Report" and "Variables".
export const divisions = [
  {
    id: "aiml",
    name: "AI and ML business unit",

    reports: [
      {
        id: "compressor-performance",
        name: "Compressor performance",

        variables: [
          "buyer_name",
          "org_name",
          "model",
          "region",
        ],
      },

      {
        id: "model-efficiency-trend",
        name: "Model efficiency trend",

        variables: [
          "model",
          "region",
          "quarter",
        ],
      },
    ],
  },

  {
    id: "recip",
    name: "Reciprocating Compressors",

    reports: [
      {
        id: "production-output",
        name: "Production output",

        variables: [
          "buyer_name",
          "org_name",
          "model",
        ],
      },

      {
        id: "warranty-claims",
        name: "Warranty claims",

        variables: [
          "buyer_name",
          "region",
          "quarter",
        ],
      },
    ],
  },

  {
    id: "centrifugal",
    name: "Centrifugal Compressors",

    reports: [
      {
        id: "throughput-summary",
        name: "Throughput summary",

        variables: [
          "org_name",
          "model",
          "region",
        ],
      },
    ],
  },

  {
    id: "aftermarket",
    name: "After-Market Services",

    reports: [
      {
        id: "service-tickets",
        name: "Service tickets",

        variables: [
          "buyer_name",
          "region",
        ],
      },

      {
        id: "amc-renewals",
        name: "AMC renewals",

        variables: [
          "buyer_name",
          "org_name",
          "quarter",
        ],
      },
    ],
  },
];


export const lookupData = {
  buyer_name: [
    "Acme Corp",
    "Acme Industries",
    "Acme Traders Ltd",
    "Bluewave Manufacturing",
    "Delta Engineering Works",
  ],

  org_name: [
    "Kirloskar Pneumatic Co Ltd",
    "Kirloskar Oil Engines",
    "Kirloskar Ferrous",
  ],

  model: [
    "Model 546",
    "Model 7473",
    "Model 425",
    "Model 1615",
  ],

  region: [
    "West India",
    "North India",
    "South India",
    "East India",
  ],

  quarter: [
    "Q1",
    "Q2",
    "Q3",
    "Q4",
  ],
};
// Sidebar "Recent" list. `reportKey` links a history entry back to a
// reportData entry below, which is what lights up the "view report" badge.
export const historyItems = [
  { id: "h1", label: "Buyer wise output Q2", time: "2h ago", reportKey: "buyerQ2" },
  { id: "h2", label: "Org efficiency trend", time: "Yesterday", reportKey: null },
  { id: "h3", label: "Model 546 comparison", time: "Mon", reportKey: "model546" },
  { id: "h4", label: "Region-wise sales", time: "Last week", reportKey: null },
  { id: "h5", label: "Weekly summary", time: "Last week", reportKey: null },
];

// Backing data for the "View report" modal, keyed by an id referenced from
// a bot message (message.reportKey) or a history item (historyItem.reportKey).
export const reportData = {
  buyerQ2: {
    title: "Buyer wise output — Q2 (/buyer:Acme Corp)",
    columns: ["Model", "Units shipped", "Efficiency", "Region"],
    rows: [
      ["Model 546", "18", "93%", "West India"],
      ["Model 7473", "14", "90%", "West India"],
      ["Model 425", "10", "89%", "North India"],
    ],
  },
  model546: {
    title: "Model 546 comparison (/model:Model 546)",
    columns: ["Buyer", "Cycles", "Efficiency"],
    rows: [
      ["Acme Corp", "14", "93%"],
      ["Bluewave Manufacturing", "11", "85%"],
      ["Delta Engineering Works", "9", "88%"],
    ],
  },
};

// Seed conversation shown on first load, mirrors the original static demo.
export const seedMessages = [
  {
    id: "m1",
    type: "user",
    parts: [
      { text: "Show throughput for " },
      { pill: "/buyer:Acme Corp" },
      { text: " last quarter" },
    ],
  },
  {
    id: "m2",
    type: "bot",
    text: "Acme Corp shipped 42 units in Q2, averaging 91% efficiency across 3 compressor models.",
    reportKey: "buyerQ2",
  },
  {
    id: "m3",
    type: "user",
    parts: [
      { text: "Compare that against " },
      { pill: "/buyer:Bluewave Manufacturing" },
      { text: " for the same period" },
    ],
  },
  {
    id: "m4",
    type: "bot",
    text: "Bluewave Manufacturing shipped 29 units in Q2, averaging 87% efficiency, roughly 4 points below Acme Corp.",
  },
  {
    id: "m5",
    type: "user",
    parts: [{ text: "Break it down by " }, { pill: "/model:Model 546" }],
  },
  {
    id: "m6",
    type: "bot",
    text: "For Model 546 specifically, Acme Corp ran at 93% efficiency versus Bluewave's 85% across 14 recorded cycles.",
    reportKey: "model546",
  },
  {
    id: "m7",
    type: "user",
    parts: [{ text: "And for " }, { pill: "/region:West India" }, { text: " overall?" }],
  },
  {
    id: "m8",
    type: "bot",
    text: "West India recorded 312 total units in Q2 across all buyers, with an average efficiency of 89%.",
  },
];

// Mock "AI" behind the did-you-mean / related-question flow. Swap for a real
// backend call later — everything downstream just consumes text in/text out.
export function mockClarifyOptions(text) {
  return [
    `Show ${text} for the current quarter`,
    `Show ${text} broken down by region`,
    `Compare ${text} against last quarter`,
  ];
}

export function mockRelatedOptions(text) {
  return [
    `What is the trend for ${text} over the last 4 quarters?`,
    `Which buyer has the highest ${text}?`,
    `Show ${text} for all regions combined`,
  ];
}

export function isRecognizedQuery(text) {
  return /buyer|org|model|region|efficien|unit|throughput/i.test(text);
}
