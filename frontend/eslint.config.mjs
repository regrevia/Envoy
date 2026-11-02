import nextVitals from "eslint-config-next/core-web-vitals";

const config = [
  ...nextVitals,
  {
    ignores: [".next/**", ".next-dev/**", "node_modules/**", "next-env.d.ts"],
  },
  {
    rules: {
      // react-hooks v6 flags syncing local state from fetched data inside an
      // effect; the dashboard uses that pattern throughout and it is intended.
      "react-hooks/set-state-in-effect": "off",
    },
  },
];

export default config;
