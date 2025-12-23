export type DeviceState =
  | "ok"
  | "mismatch"
  | "offline"
  | "no_baseline"
  | "never_polled"
  | "files_changed"
  | "unknown"
  | string;

export interface Device {
  id: number;
  cluster_id: number;
  device_serial: string;
  supplier: string;
  device_type: string;
  line_no?: string | null;
  ip: string;
  port: number;
  protocol: string;
  path: string;
  enabled: boolean;
  last_state?: string | null;
  last_state_at?: string | null;
}

export interface Baseline {
  expected_main_version?: string | null;
  allowed_main_globs?: string[];
}

export interface Snapshot {
  id: number;
  observed_at: string;
  success: boolean;
  http_status?: number | null;
  latency_ms?: number | null;
  error?: string | null;
  main_version?: string | null;
  payload?: any;
}

export interface VersionCatalogItem {
  supplier: string;
  device_type: string;
  main_version: string;
  device_changelog_md?: string | null;
  device_released_at?: string | null;
  device_checksum?: string | null;
  device_updated_at?: string | null;
  changelog_md?: string | null; // manager note
}

export interface StatusRow {
  state: DeviceState;
  device: Device;
  baseline: Baseline | null;
  latest_snapshot: Snapshot | null;
}

export interface DeviceDetailResponse {
  device: Device;
  baseline: Baseline | null;
  latest_snapshot: Snapshot | null;
  observed_version_catalog: VersionCatalogItem | null;
  expected_version_catalog: VersionCatalogItem | null;
}

export interface DeviceDocsItem {
  name: string;
  checksum?: string | null;
  content_text?: string | null;
  encoding?: string | null;
  content_type?: string | null;
  truncated?: boolean;
  size_bytes?: number | null;
}

export interface DeviceDocsResponse {
  snapshot_id: number | null;
  items: DeviceDocsItem[];
}

export interface VersionHistoryItem {
  main_version: string;
  first_seen: string;
  last_seen: string;
  samples: number;
  device_changelog_md?: string | null;
  device_released_at?: string | null;
  device_checksum?: string | null;
  changelog_md?: string | null; // manager note
}

