FROM frappe/erpnext:v16.50.0
USER root
COPY infra/realtime_origin.conf /etc/nginx/conf.d/realtime_origin.conf
RUN sed -i 's@proxy_set_header Origin .*;@proxy_set_header Origin $realtime_origin;@' /templates/nginx/frappe.conf.template
COPY infra/patch_realtime.py /tmp/patch_realtime.py
RUN python /tmp/patch_realtime.py
COPY infra/patch_banking.py /tmp/patch_banking.py
RUN python /tmp/patch_banking.py
COPY infra/patch_trend_translations.py /tmp/patch_trend_translations.py
RUN python /tmp/patch_trend_translations.py
COPY --chown=frappe:frappe apps/warehouse_core /home/frappe/frappe-bench/apps/warehouse_core
USER frappe
RUN /home/frappe/frappe-bench/env/bin/pip install --no-deps pypdf==6.15.0 defusedxml==0.7.1 openpyxl==3.1.5
RUN /home/frappe/frappe-bench/env/bin/pip install --no-deps -e /home/frappe/frappe-bench/apps/warehouse_core
# The base image points sites/assets outside the shared sites volume. Include
# the app asset link in every service image, including the Nginx frontend.
RUN ln -s /home/frappe/frappe-bench/apps/warehouse_core/warehouse_core/public /home/frappe/frappe-bench/assets/warehouse_core
# Desk lists zone keys and resolves saved legacy names through links.
RUN printf '%s\n' 'data["zones"]["Europe/Kyiv"] = data["zones"].pop("Europe/Kiev")' \
        'data["links"]["Europe/Kiev"] = "Europe/Kyiv"' >> apps/frappe/frappe/utils/momentjs.py \
    && sed -i '/^ *"Europe\/Kiev",$/d' apps/frappe/frappe/geo/country_info.json \
    && env/bin/python -c 'from frappe.utils.momentjs import get_all_timezones; zones = get_all_timezones(); assert "Europe/Kiev" not in zones and "Europe/Kyiv" in zones'
