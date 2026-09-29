{{/*
Render a DPG/domain config string with docker-compose hostnames rewritten to
Kubernetes Service names from .Values.serviceHosts. The shared YAML under
dev-kit/ addresses blocks by their compose names (http://memory_layer:8002),
and underscores are not valid in Kubernetes Service names. Only "//<name>:"
is matched, so YAML keys are left untouched.
Usage: include "dpg.renderConfig" (dict "config" .Values.dpgConfig "hosts" .Values.serviceHosts)
*/}}
{{- define "dpg.renderConfig" -}}
{{- $cfg := .config -}}
{{- range $from, $to := .hosts -}}
{{- $cfg = replace (printf "//%s:" $from) (printf "//%s:" $to) $cfg -}}
{{- end -}}
{{- $cfg -}}
{{- end -}}
