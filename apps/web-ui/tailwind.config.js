export default {
    content: ["./index.html", "./src/**/*.{ts,tsx}"],
    theme: {
        extend: {
            colors: {
                ink: "#1f2937",
                muted: "#64748b",
                line: "#d7dde8",
                canvas: "#f6f8fb",
                cobalt: "#2563eb",
                teal: "#0f766e",
                rose: "#be123c",
                amber: "#b45309",
            },
            boxShadow: {
                panel: "0 1px 2px rgba(15, 23, 42, 0.08)",
            },
        },
    },
    plugins: [],
};
