import requests
from collections import Counter

from plugins.base_plugin.base_plugin import BasePlugin


class HubitatStatus(BasePlugin):
    """
    InkyPi plugin to display Hubitat status:
    - Current mode (from /modes)
    - HSM status (from /hsm)
    - Devices summary (from /devices/all)
    - Presence summary for mobile devices (type 'Mobile App Device')
    - Latest notifications from mobile devices (notificationText), deduped
    """

    def _get_common_config(self, device_config):
        api_base = (
            device_config.load_env_key("HUBITAT_API_BASE")
            or "http://192.168.20.9"
        )
        access_token = device_config.load_env_key("HUBITAT_ACCESS_TOKEN")

        if not access_token:
            raise RuntimeError(
                "Hubitat access token not configured. "
                "Set HUBITAT_ACCESS_TOKEN in the InkyPi .env."
            )

        verify_ssl = False

        modes_path = "/apps/api/94/modes"
        hsm_path = "/apps/api/94/hsm"
        devices_path = "/apps/api/94/devices/all"

        base = api_base.rstrip("/")
        modes_url = f"{base}{modes_path}?access_token={access_token}"
        hsm_url = f"{base}{hsm_path}?access_token={access_token}"
        devices_url = f"{base}{devices_path}?access_token={access_token}"

        return modes_url, hsm_url, devices_url, verify_ssl

    def _call_api(self, url, verify_ssl, timeout=10):
        try:
            resp = requests.get(url, verify=verify_ssl, timeout=timeout)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            content = e.response.text if e.response else "No response content"
            status = e.response.status_code if e.response else "unknown"
            raise RuntimeError(
                f"Hubitat API HTTP error {status}: {content}"
            ) from e
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Timeout calling {url}")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(
                f"Network error calling Hubitat API: {str(e)}"
            ) from e

        try:
            return resp.json()
        except ValueError as e:
            raise RuntimeError(
                f"Invalid JSON from Hubitat API at {url}"
            ) from e

    def _extract_mode_name(self, data):
        """
        /modes: list of {active, id, name, ...}
        """
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("active"):
                    return str(item.get("name", "Unknown"))
        return "Unknown"

    def _extract_hsm_status(self, data):
        """
        /hsm: usually {"hsm":"armingNight"} or {"hsmStatus": "..."}
        """
        if isinstance(data, dict):
            if "hsm" in data:
                return str(data.get("hsm"))
            if "hsmStatus" in data:
                return str(data.get("hsmStatus"))
        return "Unknown"

    def _get_devices_summary(self, data):
        """
        /devices/all: list of device dicts with at least 'type' and labels.
        Returns total count and top few type buckets.
        """
        if not isinstance(data, list):
            return 0, []

        total = len(data)
        type_counter = Counter()

        for dev in data:
            if not isinstance(dev, dict):
                continue
            dev_type = dev.get("type") or "Other"
            type_counter[dev_type] += 1

        top_types = []
        for dev_type, count in type_counter.most_common(4):
            top_types.append({"type": dev_type, "count": count})

        return total, top_types

    def _get_presence_summary(self, devices):
        """
        Derive presence from /devices/all.

        We treat any device with type "Mobile App Device" as a
        presence device, and read its 'presence' field from the
        attributes dict when present.
        """
        if not isinstance(devices, list):
            return []

        presence_items = []

        for dev in devices:
            if not isinstance(dev, dict):
                continue

            dev_type = dev.get("type") or ""
            if dev_type != "Mobile App Device":
                continue

            name = str(dev.get("label") or dev.get("name") or "Mobile device")

            attrs = dev.get("attributes") or {}
            status = "unknown"

            if isinstance(attrs, dict):
                if "presence" in attrs:
                    status = str(attrs.get("presence", "unknown"))
            elif isinstance(attrs, list):
                for attr in attrs:
                    if not isinstance(attr, dict):
                        continue
                    if attr.get("name") == "presence":
                        status = str(attr.get("currentValue", "unknown"))
                        break

            presence_items.append(
                {
                    "name": name,
                    "status": status,
                }
            )

        return presence_items

    def _get_notifications(self, devices, max_items=3, max_len=140):
        """
        Extract latest notification text from Mobile App Devices.

        Uses the 'notificationText' field in the attributes dict
        when present, removes duplicate messages, and returns only
        the text for display.
        """
        if not isinstance(devices, list):
            return []

        seen_texts = set()
        notifications = []

        for dev in devices:
            if not isinstance(dev, dict):
                continue

            dev_type = dev.get("type") or ""
            if dev_type != "Mobile App Device":
                continue

            attrs = dev.get("attributes") or {}

            text = None
            if isinstance(attrs, dict):
                text = attrs.get("notificationText")
            elif isinstance(attrs, list):
                for attr in attrs:
                    if not isinstance(attr, dict):
                        continue
                    if attr.get("name") == "notificationText":
                        text = attr.get("currentValue")
                        break

            if not text:
                continue

            text_str = str(text).strip()
            if not text_str:
                continue

            if len(text_str) > max_len:
                text_str = text_str[: max_len - 1] + "…"

            if text_str in seen_texts:
                continue
            seen_texts.add(text_str)

            notifications.append({"text": text_str})

            if len(notifications) >= max_items:
                break

        return notifications

    def generate_image(self, settings, device_config):
        title = settings.get("title", "Hubitat Status").strip() or "Hubitat Status"

        modes_url, hsm_url, devices_url, verify_ssl = self._get_common_config(
            device_config
        )
        modes_data = self._call_api(modes_url, verify_ssl)
        hsm_data = self._call_api(hsm_url, verify_ssl)
        devices_data = self._call_api(devices_url, verify_ssl)

        mode_name = self._extract_mode_name(modes_data)
        hsm_status = self._extract_hsm_status(hsm_data)
        total_devices, device_type_items = self._get_devices_summary(devices_data)
        presence_items = self._get_presence_summary(devices_data)
        notifications = self._get_notifications(devices_data)

        try:
            width, height = device_config.get_resolution()
        except Exception as e:
            raise RuntimeError(f"Failed to get display resolution: {e}")

        return self.render_image(
            dimensions=(width, height),
            html_file="hubitat_status.html",
            css_file="hubitat_status.css",
            template_params={
                "title": title,
                "mode_name": mode_name,
                "hsm_status": hsm_status,
                "total_devices": total_devices,
                "device_type_items": device_type_items,
                "presence_items": presence_items,
                "notifications": notifications,
                "modes_raw": modes_data,
                "hsm_raw": hsm_data,
                "devices_raw": devices_data,
                "plugin_settings": settings,
            },
        )

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        if "title" not in template_params:
            template_params["title"] = "Hubitat Status"
        template_params["show_hsm"] = True
        template_params["show_mode"] = True
        template_params["show_devices"] = True
        template_params["show_presence"] = True
        template_params["show_notifications"] = True
        template_params["compact_layout"] = False
        template_params["show_raw_hsm"] = False
        template_params["style_settings"] = True
        return template_params