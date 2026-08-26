# Deploy this integration straight onto a running Home Assistant, so a code
# change can be tested without cutting a release and going through HACS.
#
# Requires the "Advanced SSH & Web Terminal" add-on running with your public key
# in its authorized_keys; it ships both rsync and the `ha` CLI.
#
# Override per invocation or from the environment:
#   just ha_host=10.0.0.5 sync
#   HA_HOST=10.0.0.5 just sync
#
# WHICH COPY IS LIVE: HACS installs to the manifest domain, so canon_ccapi/ is
# the directory HA loads. A stray directory whose manifest ALSO declares
# domain: canon_ccapi shadows it - HA runs that code while HACS still reports
# itself up to date. `just status` shows what is really there; the giveaway in
# the log is a logger name that does not match the directory.
#
# LOGGING: `just restart` resets anything set via logger.set_level. For a
# debugging session put this in configuration.yaml so it survives:
#
#   logger:
#     default: warning
#     logs:
#       custom_components.canon_ccapi: debug

ha_host := env_var_or_default("HA_HOST", "192.168.178.67")
ha_user := env_var_or_default("HA_USER", "root")
ha_port := env_var_or_default("HA_PORT", "22")
remote := "/config/custom_components/canon_ccapi"

_default:
    @just --list

# List custom_components on the target and show the live manifest
status:
    ssh -p {{ha_port}} {{ha_user}}@{{ha_host}} 'ls -la /config/custom_components/ && echo "--- live manifest ---" && cat {{remote}}/manifest.json'

# Preview what deploy would change, without touching anything
deploy-dry:
    rsync -avn --delete \
        --exclude='__pycache__' --exclude='*.pyc' \
        -e 'ssh -p {{ha_port}}' \
        custom_components/canon_ccapi/ \
        {{ha_user}}@{{ha_host}}:{{remote}}/

# Overwrite the target with this working tree (--delete removes stale files)
deploy:
    rsync -av --delete \
        --exclude='__pycache__' --exclude='*.pyc' \
        -e 'ssh -p {{ha_port}}' \
        custom_components/canon_ccapi/ \
        {{ha_user}}@{{ha_host}}:{{remote}}/

# Restart HA Core - required, Python caches imported modules
restart:
    ssh -p {{ha_port}} {{ha_user}}@{{ha_host}} 'ha core restart'

# deploy + restart: the usual edit/test loop
sync: deploy restart

# Follow this integration's log lines
logs:
    ssh -p {{ha_port}} {{ha_user}}@{{ha_host}} 'ha core logs --follow' | grep --line-buffered -i canon

# Delete a shadowing copy, e.g. `just remove-stale canon`. Destructive.
remove-stale name:
    ssh -p {{ha_port}} {{ha_user}}@{{ha_host}} 'rm -rf /config/custom_components/{{name}}'
