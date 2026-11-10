"""Public EUR processing tariff. Decimal arithmetic is shared by quote and checkout."""
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING

TARIFF_VERSION = '2026-11-10'
PLANS = {
    'free': {'storage_gib': 5, 'images': 0, 'splats': 0, 'image_eur': '0.030', 'splat_eur': '3.00'},
    'pro': {'storage_gib': 250, 'images': 1000, 'splats': 3, 'image_eur': '0.020', 'splat_eur': '2.00'},
    'studio': {'storage_gib': 1000, 'images': 4000, 'splats': 12, 'image_eur': '0.015', 'splat_eur': '1.50'},
}
MINIMUM_TASK_EUR = Decimal('2.00')
IMAGE_SIZE_MIB = Decimal('12')


def money(value):
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError('Invalid amount')
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def image_quote(num_images, total_mib, remaining, unit_eur):
    count = int(num_images)
    size = Decimal(str(total_mib or 0))
    rate = Decimal(str(unit_eur))
    if count < 1 or not size.is_finite() or size < 0 or not rate.is_finite() or rate <= 0:
        raise ValueError('Invalid image count, size or tariff')
    multiplier = max(Decimal(1), size / count / IMAGE_SIZE_MIB)
    effective = int((Decimal(count) * multiplier).to_integral_value(rounding=ROUND_CEILING))
    covered = min(effective, max(0, int(remaining)))
    billable = effective - covered
    subtotal = money(rate * billable)
    total = max(MINIMUM_TASK_EUR, subtotal) if billable else Decimal(0)
    return dict(tariff_version=TARIFF_VERSION, num_images=count, effective_images=effective,
                resolution_multiplier=float(multiplier), average_image_mib=float(size / count),
                image_size_threshold_mib=float(IMAGE_SIZE_MIB), covered_images=covered,
                billable_images=billable, unit_price_eur=float(rate), subtotal_eur=float(subtotal),
                minimum_task_eur=float(MINIMUM_TASK_EUR), minimum_applied=bool(billable and subtotal < MINIMUM_TASK_EUR),
                retail_price_eur=float(total), total_price_eur=float(total), storage_unit='GiB')
