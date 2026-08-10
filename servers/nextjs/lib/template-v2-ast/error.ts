export class TemplateV2AstError extends Error {
  readonly code: string;
  readonly path: string;

  constructor(code: string, path: string) {
    super(`${code}:${path}`);
    this.name = "TemplateV2AstError";
    this.code = code;
    this.path = path;
  }
}

export function failTemplateV2Ast(code: string, path: string): never {
  throw new TemplateV2AstError(code, path);
}
