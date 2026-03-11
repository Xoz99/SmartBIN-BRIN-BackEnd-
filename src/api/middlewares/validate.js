import { ZodError } from 'zod';
import { error } from '../../utils/response.js';

/**
 * Zod validation middleware factory
 *
 * Usage:
 *   router.post('/route', validate({ body: MySchema }), controller)
 *
 * @param {{ body?: ZodSchema, params?: ZodSchema, query?: ZodSchema }} schemas
 */
export function validate(schemas) {
    return (req, res, next) => {
        try {
            if (schemas.body) {
                req.body = schemas.body.parse(req.body);
            }
            if (schemas.params) {
                req.params = schemas.params.parse(req.params);
            }
            if (schemas.query) {
                req.query = schemas.query.parse(req.query);
            }
            next();
        } catch (err) {
            if (err instanceof ZodError) {
                return error(
                    res,
                    'Validation failed',
                    422,
                    err.errors.map((e) => ({ field: e.path.join('.'), message: e.message }))
                );
            }
            next(err);
        }
    };
}
