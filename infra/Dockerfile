FROM frappe/erpnext:v16.50.0
USER root
# Preserve the browser's Origin for Frappe's same-origin authentication check.
# The upstream template substitutes the internal site name (frontend), which
# differs from the public localhost Host and rejects valid socket connections.
RUN sed -i 's@proxy_set_header Origin .*;@proxy_set_header Origin $http_origin;@' /templates/nginx/frappe.conf.template
COPY infra/patch_realtime.py /tmp/patch_realtime.py
RUN python /tmp/patch_realtime.py
COPY infra/patch_banking.py /tmp/patch_banking.py
RUN python /tmp/patch_banking.py
COPY infra/patch_trend_translations.py /tmp/patch_trend_translations.py
RUN python /tmp/patch_trend_translations.py
COPY --chown=frappe:frappe apps/warehouse_core /home/frappe/frappe-bench/apps/warehouse_core
USER frappe
RUN /home/frappe/frappe-bench/env/bin/pip install --no-deps -e /home/frappe/frappe-bench/apps/warehouse_core
# The base image points sites/assets outside the shared sites volume. Include
# the app asset link in every service image, including the Nginx frontend.
RUN ln -s /home/frappe/frappe-bench/apps/warehouse_core/warehouse_core/public /home/frappe/frappe-bench/assets/warehouse_core
