/** Geometry shared by the image analyzer and input readiness guard. No I/O. */
export const CALIBRATION_COORDINATE_SCHEMA = 2;

function boundedRectangle(rect, width, height) {
  return !!rect
    && ['left', 'right', 'top', 'bottom', 'width', 'height']
      .every(key => Number.isFinite(rect[key]))
    && rect.left >= 0 && rect.top >= 0
    && rect.right <= width && rect.bottom <= height
    && rect.width > 20 && rect.height > 20
    && Math.abs(rect.width - (rect.right - rect.left)) < 1
    && Math.abs(rect.height - (rect.bottom - rect.top)) < 1;
}

/**
 * Schema 2 binds board.top to the deadline, not the top of the visible arena.
 * A matching aspect ratio alone cannot validate an old, shifted origin.
 */
export function isUsableCalibration(cal, width = cal?.screen?.width, height = cal?.screen?.height) {
  if (!cal || cal.coordinateSchema !== CALIBRATION_COORDINATE_SCHEMA
      || cal.provisional || cal.isFallback
      || !Number.isFinite(cal.confidence) || cal.confidence < 0.6 || cal.confidence > 1
      || !Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0
      || cal.screen?.width !== width || cal.screen?.height !== height
      || !boundedRectangle(cal.board, width, height)
      || !boundedRectangle(cal.arena, width, height)) return false;

  const { board, arena, hud } = cal;
  return Math.abs(arena.left - board.left) < 1
    && Math.abs(arena.right - board.right) < 1
    && Math.abs(arena.bottom - board.bottom) < 1
    && arena.top < board.top
    && Math.abs(board.height - board.width * 8.32 / 7) <= Math.max(3, board.width * 0.03)
    && !!hud && Number.isFinite(hud.top) && Number.isFinite(hud.bottom)
    && hud.top >= 0 && hud.top < hud.bottom
    && Math.abs(hud.bottom - arena.top) < 1;
}
