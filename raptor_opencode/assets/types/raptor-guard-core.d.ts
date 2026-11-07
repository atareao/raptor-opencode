export interface WebFetchRule {
  domains?: string[];
  https_any?: boolean;
  anchor_file?: string;
}

export interface AgentPolicy {
  bash?: string[];
  webfetch?: WebFetchRule;
}

export interface PolicyDefault {
  unrestricted?: boolean;
}

export interface Policy {
  version?: number;
  raptor_home?: string;
  default?: PolicyDefault;
  hooks?: Record<string, string>;
  agents?: Record<string, AgentPolicy>;
}

export type SelectedEntry = AgentPolicy & { hooks: Record<string, string> };

export interface HookInvocation {
  command: string;
  args: string[];
  tool: "bash" | "webfetch";
}

export interface HookResult {
  code?: number | null;
  stderr?: string;
}

export interface Verdict {
  action: "allow" | "block";
  message?: string;
}

export interface ToolPayload {
  tool_name: "Bash" | "WebFetch";
  tool_input: { command?: string; url?: string };
}

export declare const BASH_HOOK: string;
export declare const WEBFETCH_HOOK: string;

export declare function selectEntry(
  policy: Policy | null | undefined,
  agent: string | undefined,
): SelectedEntry | null;

export declare function hookInvocation(
  entry: SelectedEntry | null | undefined,
  tool: string | undefined,
  raptorHome: string | undefined,
  projectDir?: string | undefined,
): HookInvocation | null;

export declare function buildPayload(tool: string | undefined, args: unknown): ToolPayload | null;

export declare function interpretResult(result: HookResult | null | undefined): Verdict;
