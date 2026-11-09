{{/*
Resource name: the release name, or "<release>-<nameSuffix>" when the chart
sets nameSuffix (used by the reach-layer channel sub-charts).
*/}}
{{- define "dpg-common.fullname" -}}
{{- if .Values.nameSuffix -}}
{{- printf "%s-%s" .Release.Name .Values.nameSuffix -}}
{{- else -}}
{{- .Release.Name -}}
{{- end -}}
{{- end -}}

{{/*
docker-compose hostnames used in the shared DPG/domain YAML -> Kubernetes
Service names (release name = chart name, all in one namespace). A chart can
replace the map with its own `serviceHosts` value, e.g. to use FQDNs such as
memory-layer.<ns>.svc.cluster.local when releases live in different namespaces.
*/}}
{{- define "dpg-common.serviceHosts" -}}
{{- if .Values.serviceHosts -}}
{{- toYaml .Values.serviceHosts -}}
{{- else -}}
agent_core: agent-core
knowledge_engine: knowledge-engine
memory_layer: memory-layer
trust_layer: trust-layer
observability_layer: observability-layer
action_gateway: action-gateway
otelcol: otel-collector
{{- end -}}
{{- end -}}

{{/*
Render a DPG/domain config string with docker-compose hostnames rewritten to
Kubernetes Service names. The shared YAML under dev-kit/ addresses blocks by
their compose names (http://memory_layer:8002), and underscores are not valid
in Kubernetes Service names. Only "//<name>:" is matched, so YAML keys are left
untouched.
Usage: include "dpg-common.renderConfig" (dict "config" $text "ctx" .)
*/}}
{{- define "dpg-common.renderConfig" -}}
{{- $cfg := .config -}}
{{- range $from, $to := (include "dpg-common.serviceHosts" .ctx | fromYaml) -}}
{{- $cfg = replace (printf "//%s:" $from) (printf "//%s:" $to) $cfg -}}
{{- end -}}
{{- $cfg -}}
{{- end -}}

{{/*
DPG framework defaults / domain overrides injected with --set-file. A chart's
own dpgConfig/domainConfig wins; otherwise global.* is used, which is how the
reach-layer parent hands one pair of files to all its channel sub-charts.
*/}}
{{- define "dpg-common.dpgConfig" -}}
{{- .Values.dpgConfig | default (dig "dpgConfig" "" (.Values.global | default dict)) -}}
{{- end -}}

{{- define "dpg-common.domainConfig" -}}
{{- .Values.domainConfig | default (dig "domainConfig" "" (.Values.global | default dict)) -}}
{{- end -}}

{{/*
Look up a dotted values path ("logLevel", "redis.url"). Missing keys render "".
Usage: include "dpg-common.valueAt" (dict "root" .Values "path" "redis.url")
*/}}
{{- define "dpg-common.valueAt" -}}
{{- $v := .root -}}
{{- range splitList "." .path -}}
{{- if kindIs "map" $v -}}
{{- $v = index $v . -}}
{{- else -}}
{{- $v = "" -}}
{{- end -}}
{{- end -}}
{{- if not (kindIs "invalid" $v) -}}{{- $v -}}{{- end -}}
{{- end -}}

{{/*
"true" when a dotted values path holds a truthy value ("false" and "" are not).
*/}}
{{- define "dpg-common.truthy" -}}
{{- $v := include "dpg-common.valueAt" . -}}
{{- if and $v (ne $v "false") -}}true{{- end -}}
{{- end -}}

{{/*
Value at a dotted path, failing the render when it is empty.
Usage: include "dpg-common.requiredAt" (dict "root" .Values "path" "auth.googleClientId" "when" "auth.enabled")
*/}}
{{- define "dpg-common.requiredAt" -}}
{{- $v := include "dpg-common.valueAt" . -}}
{{- if not $v -}}
{{- if .when -}}
{{- fail (printf "%s is required when %s is true" .path .when) -}}
{{- else -}}
{{- fail (printf "%s is required" .path) -}}
{{- end -}}
{{- end -}}
{{- $v -}}
{{- end -}}

{{/*
True when the chart mounts DPG/domain config (configFile is set).
*/}}
{{- define "dpg-common.hasConfig" -}}
{{- if .Values.configFile -}}true{{- end -}}
{{- end -}}

{{/*
True when the chart declares secret env vars or extraSecrets, i.e. when the
<name>-secrets Secret is rendered and loaded with envFrom.
*/}}
{{- define "dpg-common.hasSecret" -}}
{{- if or .Values.secretEnvFromValues (hasKey .Values "extraSecrets") -}}true{{- end -}}
{{- end -}}

{{/*
Comma-separated value keys from secretEnvFromValues, for error messages.
*/}}
{{- define "dpg-common.secretKeys" -}}
{{- $keys := list -}}
{{- range .Values.secretEnvFromValues }}{{- $keys = append $keys .key -}}{{- end -}}
{{- join ", " $keys -}}
{{- end -}}

{{/*
Where dpg.yaml and the domain file are mounted. Defaults to
<configFolder>/dpg.yaml and <configFolder>/<configFile>; a chart can move
either with config.dpgMountPath / config.domainMountPath.
*/}}
{{- define "dpg-common.configFolder" -}}
{{- .Values.configFolder | default "/app/config" -}}
{{- end -}}

{{- define "dpg-common.dpgMountPath" -}}
{{- dig "dpgMountPath" "" (.Values.config | default dict) | default (printf "%s/dpg.yaml" (include "dpg-common.configFolder" .)) -}}
{{- end -}}

{{- define "dpg-common.domainMountPath" -}}
{{- dig "domainMountPath" "" (.Values.config | default dict) | default (printf "%s/%s" (include "dpg-common.configFolder" .) .Values.configFile) -}}
{{- end -}}
