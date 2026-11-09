{{/*
ConfigMaps holding the DPG framework defaults (dpg.yaml) and the domain
overrides (<configFile>), with compose hostnames rewritten. Rendered only when
the chart sets configFile.
Usage, one per file in the service chart (the deployment's checksum
annotations hash these files):
  templates/configmap-dpg.yaml:    {{ include "dpg-common.configmap-dpg" . }}
  templates/configmap-domain.yaml: {{ include "dpg-common.configmap-domain" . }}
*/}}
{{- define "dpg-common.configmap-dpg" -}}
{{- if include "dpg-common.hasConfig" . -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "dpg-common.fullname" . }}-dpg-config
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ include "dpg-common.fullname" . }}
data:
  dpg.yaml: |
{{ include "dpg-common.renderConfig" (dict "config" (include "dpg-common.dpgConfig" .) "ctx" .) | indent 4 }}
{{- end -}}
{{- end -}}

{{- define "dpg-common.configmap-domain" -}}
{{- if include "dpg-common.hasConfig" . -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "dpg-common.fullname" . }}-domain-config
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ include "dpg-common.fullname" . }}
data:
  {{ .Values.configFile }}: |
{{ include "dpg-common.renderConfig" (dict "config" (include "dpg-common.domainConfig" .) "ctx" .) | indent 4 }}
{{- end -}}
{{- end -}}
