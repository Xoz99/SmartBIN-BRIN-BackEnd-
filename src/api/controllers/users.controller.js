import { getAllUsers, getUserById, createUserWithArea, updateUser, deleteUser } from '../../services/user.service.js';
import { success, error } from '../../utils/response.js';

export async function getUsersController(req, res) {
    const users = await getAllUsers();
    return success(res, users, 'Users retrieved');
}

export async function getUserByIdController(req, res) {
    const user = await getUserById(req.params.id);
    if (!user) return error(res, 'User not found', 404);
    return success(res, user);
}

export async function createUserController(req, res) {
    try {
        const user = await createUserWithArea(req.body);
        return success(res, user, 'User created', 201);
    } catch (err) {
        if (err.code === 'P2002') return error(res, 'Email already exists', 409);
        throw err;
    }
}

export async function updateUserController(req, res) {
    try {
        const user = await updateUser(req.params.id, req.body);
        return success(res, user, 'User updated');
    } catch (err) {
        if (err.code === 'P2025') return error(res, 'User not found', 404);
        if (err.code === 'P2002') return error(res, 'Email already exists', 409);
        throw err;
    }
}

export async function deleteUserController(req, res) {
    try {
        await deleteUser(req.params.id);
        return success(res, null, 'User deleted');
    } catch (err) {
        if (err.code === 'P2025') return error(res, 'User not found', 404);
        throw err;
    }
}
