def normalize_order_status(summary_data):
    if not summary_data:
        return "NEW"

    if summary_data.get("siparis_var_mi") is False:
        return "CANCELLED"

    return summary_data.get("status", "NEW")
