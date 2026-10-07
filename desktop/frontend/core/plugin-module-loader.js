export async function importPluginModule(source) {
  const url = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
  try { return await import(url); }
  finally { URL.revokeObjectURL(url); }
}
