import emesoftLogoLight from '../../assets/emesoft-logo-light.png'
import emesoftWordmarkLight from '../../assets/emesoft-wordmark-light.png'

/**
 * The real Emesoft logo, cropped from the company's own artwork (not redrawn). It appears on the
 * sign-in screen only — inside the app the product has its own mark (see components/Sparky.tsx),
 * with a "by Emesoft" credit in the nav rail's footer.
 *
 * The source art's wordmark and tagline are near-black, which disappears against the sign-in
 * panel, so these "-light" assets are the same artwork with those dark pixels recolored to white
 * (the red mark is untouched) — it sits directly on the dark ground with no backing plate.
 */
export function BrandMark({
  variant = 'wordmark',
  height = 28,
}: {
  /** `wordmark`: mark + "EMESOFT". `full`: also the "Emerging your Business" tagline. */
  variant?: 'wordmark' | 'full'
  height?: number
}) {
  return (
    <img
      src={variant === 'full' ? emesoftLogoLight : emesoftWordmarkLight}
      alt="Emesoft"
      style={{ height, width: 'auto' }}
    />
  )
}
