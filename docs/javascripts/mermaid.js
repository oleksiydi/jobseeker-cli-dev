document$.subscribe(() => {
  if (typeof mermaid === "undefined") {
    return;
  }
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
  });
  mermaid.run();
});
