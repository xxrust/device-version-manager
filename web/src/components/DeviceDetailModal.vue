<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { apiJson, jsonBody } from "../api";
import type { DeviceDetailResponse, DeviceDocsItem, DeviceDocsResponse, VersionHistoryItem } from "../types";

const props = defineProps<{
  deviceId: number;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "updated"): void;
}>();

const loading = ref(true);
const error = ref<string | null>(null);
const detail = ref<DeviceDetailResponse | null>(null);
const docs = ref<DeviceDocsResponse | null>(null);
const history = ref<VersionHistoryItem[]>([]);

const selectedVersion = ref<string>("");
const managerNote = ref<string>("");
const lineNo = ref<string>("");
const docName = ref<string>("");

const aiProvider = ref<string>("ollama");
const aiModel = ref<string>("qwen2.5:7b");
const aiIncludeDocs = ref<boolean>(true);
const aiTimeoutS = ref<number>(120);
const aiMaxTokens = ref<number>(1200);
const aiRunning = ref<boolean>(false);
const aiError = ref<string | null>(null);
const aiResult = ref<any>(null);

const selectedHistoryItem = computed(() => history.value.find((x) => x.main_version === selectedVersion.value) || null);

const observedCatalog = computed(() => detail.value?.observed_version_catalog || null);
const observedVersion = computed(() => detail.value?.latest_snapshot?.main_version || "");

const deviceChangelog = computed(() => {
  const v = selectedVersion.value;
  if (observedCatalog.value && observedCatalog.value.main_version === v) return observedCatalog.value.device_changelog_md || "";
  return selectedHistoryItem.value?.device_changelog_md || "";
});

const deviceChangelogMeta = computed(() => {
  const v = selectedVersion.value;
  const src =
    observedCatalog.value && observedCatalog.value.main_version === v ? observedCatalog.value : selectedHistoryItem.value;
  if (!src) return "";
  const parts: string[] = [];
  if ((src as any).device_released_at) parts.push(`released_at=${(src as any).device_released_at}`);
  if ((src as any).device_checksum) parts.push(`checksum=${(src as any).device_checksum}`);
  if ((src as any).device_updated_at) parts.push(`device_updated_at=${(src as any).device_updated_at}`);
  return parts.join(" | ");
});

const selectedDoc = computed<DeviceDocsItem | null>(() => {
  const items = docs.value?.items || [];
  return items.find((x) => x.name === docName.value) || null;
});

async function loadAll() {
  loading.value = true;
  error.value = null;
  try {
    const d = await apiJson<DeviceDetailResponse>(`/api/v1/devices/${props.deviceId}`);
    detail.value = d;
    lineNo.value = String(d.device.line_no || "");

    const h = await apiJson<{ items: VersionHistoryItem[] }>(`/api/v1/devices/${props.deviceId}/version-history?limit=200`);
    history.value = h.items || [];

    const dr = await apiJson<DeviceDocsResponse>(`/api/v1/devices/${props.deviceId}/docs`);
    docs.value = dr;

    const versions = (history.value || []).map((x) => x.main_version).filter(Boolean);
    if (observedVersion.value && !versions.includes(observedVersion.value)) versions.unshift(observedVersion.value);
    selectedVersion.value = observedVersion.value || versions[0] || "";

    if (docs.value?.items?.length) docName.value = docs.value.items[0].name;
    else docName.value = "";

    syncManagerNote();
  } catch (e: any) {
    error.value = String(e?.message || e || "load_failed");
  } finally {
    loading.value = false;
  }
}

function syncManagerNote() {
  const v = selectedVersion.value;
  if (!v) {
    managerNote.value = "";
    return;
  }
  if (observedCatalog.value && observedCatalog.value.main_version === v) {
    managerNote.value = observedCatalog.value.changelog_md || "";
    return;
  }
  managerNote.value = selectedHistoryItem.value?.changelog_md || "";
}

watch(selectedVersion, () => syncManagerNote());

async function saveLineNo() {
  if (!detail.value) return;
  error.value = null;
  await apiJson(`/api/v1/devices/${props.deviceId}`, { method: "PUT", ...jsonBody({ line_no: lineNo.value.trim() }) });
  emit("updated");
  await loadAll();
}

async function pollDevice() {
  error.value = null;
  await apiJson(`/api/v1/poll`, { method: "POST", ...jsonBody({ device_ids: [props.deviceId] }) });
  emit("updated");
  await loadAll();
}

async function setBaselineToObserved() {
  if (!detail.value) return;
  const observed = String(detail.value.latest_snapshot?.main_version || "").trim();
  if (!observed) return;
  error.value = null;
  await apiJson(`/api/v1/baselines`, {
    method: "POST",
    ...jsonBody({
      cluster_id: detail.value.device.cluster_id,
      supplier: detail.value.device.supplier,
      device_type: detail.value.device.device_type,
      expected_main_version: observed,
      note: `set_from_device ${detail.value.device.device_serial} ${new Date().toISOString()}`,
    }),
  });
  emit("updated");
  await loadAll();
}

async function saveManagerNote() {
  if (!detail.value) return;
  const supplier = detail.value.device.supplier;
  const device_type = detail.value.device.device_type;
  const main_version = selectedVersion.value.trim();
  if (!supplier || !device_type || !main_version) return;
  const changelog_md = managerNote.value.trim() ? managerNote.value : null;
  error.value = null;
  await apiJson(`/api/v1/version-catalog`, {
    method: "POST",
    ...jsonBody({ supplier, device_type, main_version, changelog_md }),
  });
  emit("updated");
  await loadAll();
}

async function runAiAnalysis() {
  aiRunning.value = true;
  aiError.value = null;
  aiResult.value = null;
  try {
    const resp = await apiJson<{ ok: boolean; provider: string; model: string; result: any }>(`/api/v1/analyze/device`, {
      method: "POST",
        ...jsonBody({
          device_id: props.deviceId,
          provider: aiProvider.value,
          model: aiModel.value,
          include_docs: aiIncludeDocs.value,
          timeout_s: aiTimeoutS.value,
          max_tokens: aiMaxTokens.value,
        }),
      });
      aiResult.value = resp.result;
  } catch (e: any) {
    aiError.value = String(e?.message || e || "analysis_failed");
  } finally {
    aiRunning.value = false;
  }
}

onMounted(loadAll);
</script>

<template>
  <div class="modalOverlay" @click.self="emit('close')">
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modalHd">
        <div class="title">
          <h1 style="margin: 0; font-size: 16px">
            {{ detail?.device.device_serial || `Device #${deviceId}` }}
            <span class="muted">(#{{ detail?.device.id ?? deviceId }})</span>
          </h1>
          <div class="meta">
            <span class="mono">{{ detail?.device.supplier }}/{{ detail?.device.device_type }}</span>
            <span class="muted" v-if="detail?.device.cluster_id"> · cluster {{ detail.device.cluster_id }}</span>
          </div>
        </div>
        <div class="row">
          <button class="btn" @click="emit('close')">关闭</button>
        </div>
      </div>

      <div class="modalBd">
        <div v-if="loading" class="muted">加载中...</div>
        <div v-else-if="error" class="muted">Error: {{ error }}</div>
        <template v-else-if="detail">
          <div class="row">
            <span class="pill">{{ detail.device.ip }}:{{ detail.device.port }}</span>
            <span class="pill">{{ detail.device.protocol }}</span>
            <span class="pill">{{ detail.device.path }}</span>
            <span class="pill" v-if="detail.latest_snapshot?.observed_at">最近拉取 {{ detail.latest_snapshot.observed_at }}</span>
            <span class="pill" v-if="detail.latest_snapshot?.main_version">v={{ detail.latest_snapshot.main_version }}</span>
          </div>

          <div class="card">
            <div style="padding: 12px">
              <div class="row" style="justify-content: space-between">
                <div class="sectionTitle">设备信息</div>
                <div class="row">
                  <button class="btn" @click="pollDevice">轮询</button>
                  <button class="btn" @click="setBaselineToObserved" :disabled="!detail.latest_snapshot?.main_version">
                    设置基线 = 当前版本
                  </button>
                  <button class="btn primary" @click="saveLineNo">保存产线号</button>
                </div>
              </div>
              <div class="row" style="margin-top: 10px">
                <label class="muted" style="font-size: 12px">产线号（可编辑）</label>
                <input class="input mono" v-model="lineNo" placeholder="Line-01 / A1" />
              </div>
              <div class="row" style="margin-top: 10px">
                <span class="muted" style="font-size: 12px">基线</span>
                <span class="pill" v-if="detail.baseline?.expected_main_version" title="expected_main_version">
                  {{ detail.baseline.expected_main_version }}
                  <span v-if="detail.baseline.allowed_main_globs?.length" class="muted">
                    （允许：{{ detail.baseline.allowed_main_globs.join(", ") }}）
                  </span>
                </span>
                <span class="pill muted" v-else>no_baseline</span>
              </div>
            </div>
          </div>

          <div class="card">
            <div style="padding: 12px">
              <div class="sectionTitle">版本信息</div>
              <div class="row" style="margin-top: 10px">
                <label class="muted" style="font-size: 12px">选择版本</label>
                <select class="select mono" v-model="selectedVersion">
                  <option v-for="it in history" :key="it.main_version" :value="it.main_version">{{ it.main_version }}</option>
                  <option v-if="observedVersion && !history.some((x) => x.main_version === observedVersion)" :value="observedVersion">
                    {{ observedVersion }}
                  </option>
                </select>
              </div>

              <div style="margin-top: 10px" class="muted" v-if="deviceChangelogMeta">{{ deviceChangelogMeta }}</div>
              <div style="margin-top: 10px">
                <div class="muted" style="font-size: 12px">设备上报更新信息（只读）</div>
                <div class="pre" style="margin-top: 6px">{{ deviceChangelog || "" }}</div>
              </div>

              <div style="margin-top: 12px">
                <div class="muted" style="font-size: 12px">管理器备注（可编辑）</div>
                <textarea class="textarea mono" rows="8" style="width: 100%; margin-top: 6px" v-model="managerNote" />
              </div>

              <div class="row" style="margin-top: 10px">
                <button class="btn primary" @click="saveManagerNote">保存备注</button>
                <span class="muted" style="font-size: 12px">备注写入管理器；设备上报内容不会被覆盖。</span>
              </div>
            </div>
          </div>

          <div class="card">
            <div style="padding: 12px">
              <div class="sectionTitle">设备文档（docs）</div>
              <div class="row" style="margin-top: 10px">
                <label class="muted" style="font-size: 12px">选择文档</label>
                <select class="select mono" v-model="docName" :disabled="!(docs?.items?.length)">
                  <option v-if="!docs?.items?.length" value="">(no docs)</option>
                  <option v-for="d in docs?.items || []" :key="d.name" :value="d.name">{{ d.name }}</option>
                </select>
                <span class="muted" style="font-size: 12px" v-if="docs?.snapshot_id">snapshot_id={{ docs.snapshot_id }}</span>
              </div>
              <div class="muted" style="font-size: 12px; margin-top: 8px" v-if="selectedDoc">
                <span v-if="selectedDoc.content_type">type={{ selectedDoc.content_type }}</span>
                <span v-if="selectedDoc.encoding"> · enc={{ selectedDoc.encoding }}</span>
                <span v-if="selectedDoc.size_bytes !== null && selectedDoc.size_bytes !== undefined"> · bytes={{ selectedDoc.size_bytes }}</span>
                <span v-if="selectedDoc.truncated"> · truncated=true</span>
                <span v-if="selectedDoc.checksum"> · {{ selectedDoc.checksum }}</span>
              </div>
              <div class="pre" style="margin-top: 10px">{{ selectedDoc?.content_text || "" }}</div>
            </div>
          </div>

          <div class="card">
            <div style="padding: 12px">
              <div class="sectionTitle">AI 分析（LangGraph）</div>
              <div class="row" style="margin-top: 10px">
                <label class="muted" style="font-size: 12px">provider</label>
                <select class="select mono" v-model="aiProvider">
                  <option value="ollama">ollama（本地）</option>
                  <option value="openai">openai（远端）</option>
                </select>
                <label class="muted" style="font-size: 12px">model</label>
                <input class="input mono" v-model="aiModel" style="min-width: 220px" placeholder="qwen2.5:7b / gpt-4o-mini" />
                <label class="muted" style="font-size: 12px">include_docs</label>
                <input type="checkbox" v-model="aiIncludeDocs" />
                <label class="muted" style="font-size: 12px">timeout_s</label>
                <input class="input mono" type="number" v-model.number="aiTimeoutS" min="5" max="600" style="width: 90px" />
                <label class="muted" style="font-size: 12px">max_tokens</label>
                <input
                  class="input mono"
                  type="number"
                  v-model.number="aiMaxTokens"
                  min="256"
                  max="8192"
                  step="256"
                  style="width: 110px"
                />
                <button class="btn primary" @click="runAiAnalysis" :disabled="aiRunning">运行分析</button>
              </div>
              <div class="muted" style="font-size: 12px; margin-top: 8px" v-if="aiError">Error: {{ aiError }}</div>
              <div class="pre" style="margin-top: 10px" v-if="aiResult">{{ JSON.stringify(aiResult, null, 2) }}</div>
              <div class="muted" style="font-size: 12px; margin-top: 10px" v-else>
                说明：`ollama` 需要本地 Ollama 运行；`openai` 需要设置 `OPENAI_API_KEY`。
              </div>
            </div>
          </div>

          <details class="card">
            <summary style="padding: 12px; cursor: pointer"><span class="sectionTitle">原始 JSON（latest_snapshot.payload）</span></summary>
            <div style="padding: 12px; padding-top: 0">
              <div class="pre">{{ JSON.stringify(detail.latest_snapshot?.payload || null, null, 2) }}</div>
            </div>
          </details>
        </template>
      </div>
    </div>
  </div>
</template>
