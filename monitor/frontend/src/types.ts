export interface Meta {
  schema_version?: number;
  health?: string;
  timezone?: string;
  stale_after_seconds?: number;
  data_updated_at?: string | null;
  evidence_updated_at?: string | null;
  heartbeat?: string | null;
  heartbeat_utc?: string | null;
  last_api_attempt_utc?: string | null;
  api_in_flight?: boolean;
  config_ok?: boolean;
  configuration_error?: string | null;
  sdk_ready?: boolean;
  worker_alive?: boolean;
  sample_count?: number;
  day_count?: number;
  statistics_window?: { kind?: string; retained?: boolean; cache_ttl_seconds?: number };
  auto_probes?: boolean;
  ready?: boolean;
  cancellation_supported?: boolean;
  success_5m_metric?: string;
  [key: string]: unknown;
}

export interface Run {
  id: string;
  account: string;
  account_id?: string;
  account_name?: string;
  status?: string;
  state?: string;
  name?: string;
  scheduled_at?: string | null;
  submitted_at?: string | null;
  submission_ack_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  terminal_observed_at?: string | null;
  push_end_at?: string | null;
  reconciled_at_utc?: string | null;
  queue_estimate_seconds?: number | null;
  queue_lower_seconds?: number | null;
  queue_upper_seconds?: number | null;
  submit_to_start_seconds?: number | null;
  submit_to_start_lower_seconds?: number | null;
  submit_to_start_upper_seconds?: number | null;
  t4x2?: boolean | null;
  gpu_names?: string[] | null;
  observation_timed_out?: boolean;
  over_600_observation?: boolean;
  unacceptable?: boolean;
  submit_to_start_unacceptable?: boolean;
  skip_reason?: string | null;
  error?: string | null;
  version?: number | null;
  [key: string]: unknown;
}

export interface Account {
  id: string;
  name: string;
  username?: string;
  status?: string;
  state?: string;
  sample_count?: number;
  day_count?: number;
  active_runs?: number;
  blocked?: number;
  queue_estimate_seconds?: number | null;
  queue_lower_seconds?: number | null;
  queue_upper_seconds?: number | null;
  updated_at?: string | null;
  active_run_id?: string | null;
  next_probe_utc?: string | null;
  latest_run?: Run | null;
}

export interface HourStat {
  hour: number;
  day_count: number;
  sample_count: number;
  scheduled: number;
  submitted: number;
  successful_t4x2: number;
  within_5m_t4x2: number;
  denominator: number;
  success_5m_rate: number | null;
  queue_sample_count: number;
  median_queue_seconds: number | null;
  p90_seconds: number | null;
  p50_seconds: number | null;
  metric?: string;
  submit_to_start_p50_seconds: number | null;
  submit_to_start_p90_seconds: number | null;
  failures: number;
  incomplete: number;
  skipped: number;
  unknown_queue: number;
  unacceptable: number;
  rank_eligible: boolean;
  rank: number | null;
}

export interface ApiCall {
  id?: number;
  run_id?: string;
  operation?: string;
  at?: number;
  latency_ms?: number;
  ok?: number;
  http_status?: number | null;
  error?: string | null;
}

export interface Summary {
  schema_version?: number;
  meta: Meta;
  hours: HourStat[];
  hourly?: HourStat[];
  timezone?: string;
  generated_at?: string;
  auto_probes?: boolean;
  ready?: boolean;
  accounts: Account[];
  runs: Run[];
  api_calls?: ApiCall[];
  minimum_rank_days?: number;
  statistics?: { day_count?: number; sample_count?: number; total_runs?: number; skipped?: number };
  definitions?: Record<string, string>;
}

export interface LogicNode {
  id: string;
  kind?: string;
  label?: string;
  active?: boolean;
  terminal?: boolean;
  description?: string;
  occupancy?: number | null;
  active_occupancy?: number | null;
}

export interface LogicEdge {
  from?: string;
  to?: string;
  source?: string;
  target?: string;
  label?: string;
  description?: string;
}

export interface LogicOccupancy {
  total: number;
  active_total: number;
  by_state: Record<string, number>;
  active_by_state: Record<string, number>;
}

export interface LogicResponse {
  schema_version?: number;
  generated_at?: string;
  states?: string[];
  active_states?: string[];
  terminal_states?: string[];
  provider_states?: string[];
  cancellation_supported?: boolean;
  nodes: LogicNode[];
  edges: LogicEdge[];
  occupancy?: LogicOccupancy;
  counts?: Record<string, number | null> | LogicOccupancy;
  notes?: string;
}

export interface RoadmapIssue {
  number: number | string;
  title?: string;
  state?: string;
  url?: string;
  body?: string;
  updatedAt?: string;
  updated_at?: string;
  labels?: unknown[];
}

export interface RoadmapDependency {
  from: number | string;
  to: number | string;
  label?: string;
}

export interface RoadmapSnapshot {
  repo?: string;
  synced_at?: string;
  syncedAt?: string;
  issues?: RoadmapIssue[];
  dependencies?: RoadmapDependency[];
  blocked?: (number | string)[];
}

export interface RoadmapResponse extends Partial<RoadmapSnapshot> {
  schema_version?: number;
  generated_at?: string;
  available?: boolean;
  reason?: string | null;
  source?: string;
  snapshot?: RoadmapSnapshot | null;
  [key: string]: unknown;
}

export interface Health {
  schema_version?: number;
  ok: boolean;
  worker_alive?: boolean;
  ready?: boolean;
  auto_probes?: boolean;
  heartbeat_utc?: string | null;
  tick_started_utc?: string | null;
  tick_finished_utc?: string | null;
  api_in_flight?: boolean;
  last_api_attempt_utc?: string | null;
  last_api_result_utc?: string | null;
  sdk_ready?: boolean;
  config_ok?: boolean;
  configuration_error?: string | null;
  worker_error?: string | null;
  progress?: string;
  watchdog?: string | null;
  fatal_reason?: string | null;
  readiness_attempts?: number;
  readiness?: { ok?: boolean; error?: string | null; sdk_version?: string; version_requests?: boolean };
}

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  lastLoaded: number | null;
  loading: boolean;
}
