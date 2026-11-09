{{/*
Deployment for one DPG service.
Usage: templates/deployment.yaml: {{ include "dpg-common.deployment" . }}

Values read (all optional unless noted):
  image.repository (required), image.tag, image.pullPolicy, replicaCount
  containerName                  default: resource name
  service.port (required)        container port, also used by the probes
  configFile                     domain file name; enables config ConfigMaps/mounts
  configFolder, config.{dpgMountPath, domainMountPath, setConfigFolderEnv}
  env: ordered list, each item one of
    {name, value}                          literal
    {name, key}                            dotted values path, e.g. redis.url
    {name, secretKeyRef: {name, key}}      both fields are values paths
    plus optional ifSet (omit when empty), when (values path that must be
    true), required (fail when empty)
  secretEnvFromValues / extraSecrets      -> <name>-secrets via envFrom
  persistence                             -> PVC mounts (see _pvc.tpl)
  waitFor (list of URLs), waitImage       -> wait-for-dependencies init container
  probes.path                             default /health; "" disables probes
  healthCheck.{readiness,liveness}.{initialDelaySeconds,periodSeconds}
  resources, strategy, podSecurityContext
*/}}
{{- define "dpg-common.deployment" -}}
{{- $name := include "dpg-common.fullname" . -}}
{{- $hasConfig := include "dpg-common.hasConfig" . -}}
{{- $setConfigFolder := dig "setConfigFolderEnv" true (.Values.config | default dict) -}}
{{- $probePath := "/health" -}}
{{- if hasKey .Values "probes" }}{{ $probePath = .Values.probes.path }}{{ end -}}
{{- $hc := .Values.healthCheck | default dict -}}
{{- $ready := $hc.readiness | default (dict "initialDelaySeconds" 15 "periodSeconds" 10) -}}
{{- $live := $hc.liveness | default (dict "initialDelaySeconds" 30 "periodSeconds" 20) -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $name }}
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ $name }}
spec:
  {{- with .Values.strategy }}
  strategy:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  replicas: {{ .Values.replicaCount | default 1 }}
  selector:
    matchLabels:
      app: {{ $name }}
  template:
    metadata:
      {{- if $hasConfig }}
      # Roll pods when rendered config changes; services read it only at startup.
      annotations:
        checksum/configmap-dpg: {{ include (print $.Template.BasePath "/configmap-dpg.yaml") . | sha256sum }}
        checksum/configmap-domain: {{ include (print $.Template.BasePath "/configmap-domain.yaml") . | sha256sum }}
      {{- end }}
      labels:
        app: {{ $name }}
    spec:
      {{- with .Values.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.waitFor }}
      # docker-compose `depends_on: service_healthy` equivalent.
      initContainers:
        - name: wait-for-dependencies
          image: {{ $.Values.waitImage | default "busybox:1.36" | quote }}
          command:
            - sh
            - -c
            - |
              {{- range . }}
              until wget -q -T 3 -O /dev/null {{ . }}; do echo "waiting for {{ . }}"; sleep 2; done
              {{- end }}
      {{- end }}
      containers:
        - name: {{ .Values.containerName | default $name }}
          image: "{{ required "image.repository is required" .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy | default "IfNotPresent" }}
          ports:
            - containerPort: {{ required "service.port is required" .Values.service.port }}
          {{- if include "dpg-common.hasSecret" . }}
          envFrom:
            - secretRef:
                name: {{ $name }}-secrets
          {{- end }}
          {{- if or (and $hasConfig $setConfigFolder) .Values.env }}
          env:
            {{- if and $hasConfig $setConfigFolder }}
            - name: CONFIG_FOLDER
              value: {{ include "dpg-common.configFolder" . | quote }}
            {{- end }}
            {{- range .Values.env }}
            {{- if or (not .when) (include "dpg-common.truthy" (dict "root" $.Values "path" .when)) }}
            {{- if .secretKeyRef }}
            - name: {{ .name }}
              valueFrom:
                secretKeyRef:
                  name: {{ include "dpg-common.requiredAt" (dict "root" $.Values "path" .secretKeyRef.name "when" .when) | quote }}
                  key: {{ include "dpg-common.requiredAt" (dict "root" $.Values "path" .secretKeyRef.key "when" .when) | quote }}
            {{- else if hasKey . "value" }}
            - name: {{ .name }}
              value: {{ .value | quote }}
            {{- else }}
            {{- $val := "" }}
            {{- if .required }}
            {{- $val = include "dpg-common.requiredAt" (dict "root" $.Values "path" .key "when" .when) }}
            {{- else }}
            {{- $val = include "dpg-common.valueAt" (dict "root" $.Values "path" .key) }}
            {{- end }}
            {{- if or $val (not .ifSet) }}
            - name: {{ .name }}
              value: {{ $val | quote }}
            {{- end }}
            {{- end }}
            {{- end }}
            {{- end }}
          {{- end }}
          {{- if or $hasConfig .Values.persistence }}
          volumeMounts:
            {{- if $hasConfig }}
            - name: dpg-config
              mountPath: {{ include "dpg-common.dpgMountPath" . }}
              subPath: dpg.yaml
            - name: domain-config
              mountPath: {{ include "dpg-common.domainMountPath" . }}
              subPath: {{ .Values.configFile }}
            {{- end }}
            {{- range $key, $pv := .Values.persistence }}
            - name: {{ $key }}
              mountPath: {{ required (printf "persistence.%s.mountPath is required" $key) $pv.mountPath }}
            {{- end }}
          {{- end }}
          {{- with .Values.resources }}
          resources:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- if $probePath }}
          readinessProbe:
            httpGet:
              path: {{ $probePath }}
              port: {{ .Values.service.port }}
            initialDelaySeconds: {{ $ready.initialDelaySeconds }}
            periodSeconds: {{ $ready.periodSeconds }}
          livenessProbe:
            httpGet:
              path: {{ $probePath }}
              port: {{ .Values.service.port }}
            initialDelaySeconds: {{ $live.initialDelaySeconds }}
            periodSeconds: {{ $live.periodSeconds }}
          {{- end }}
      {{- if or $hasConfig .Values.persistence }}
      volumes:
        {{- if $hasConfig }}
        - name: dpg-config
          configMap:
            name: {{ $name }}-dpg-config
        - name: domain-config
          configMap:
            name: {{ $name }}-domain-config
        {{- end }}
        {{- range $key, $pv := .Values.persistence }}
        - name: {{ $key }}
          persistentVolumeClaim:
            claimName: {{ $name }}-{{ $key }}
        {{- end }}
      {{- end }}
{{- end -}}
