/**
 * Standard API response helpers
 */

/**
 * @param {import('express').Response} res
 * @param {object} data
 * @param {string} [message]
 * @param {number} [statusCode]
 */
export function success(res, data = null, message = 'Success', statusCode = 200) {
    return res.status(statusCode).json({
        success: true,
        message,
        data,
    });
}

/**
 * @param {import('express').Response} res
 * @param {string} [message]
 * @param {number} [statusCode]
 * @param {object|null} [error]
 */
export function error(res, message = 'An error occurred', statusCode = 500, error = null) {
    const body = {
        success: false,
        message,
        data: null,
    };
    if (error && process.env.NODE_ENV !== 'production') {
        body.error = error;
    }
    return res.status(statusCode).json(body);
}

/**
 * Paginated success helper
 */
export function paginated(res, items, total, page, limit, message = 'Success') {
    return res.status(200).json({
        success: true,
        message,
        data: items,
        pagination: {
            total,
            page,
            limit,
            totalPages: Math.ceil(total / limit),
        },
    });
}
